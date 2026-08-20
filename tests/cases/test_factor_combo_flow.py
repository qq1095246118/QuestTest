"""组合因子真实登记与刷新编排的离线单元测试。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest
import requests

from config.settings import FactorComboSettings
from service.factor_combo_service import (
    FactorComboFlowError,
    FactorComboService,
    FlowOutcome,
    RealPipelineResult,
    RealRun,
    SubmittedForm,
    TestResourceScope as ResourceScope,
)


class StubResponse:
    """提供可控状态码和 JSON 正文的 HTTP 响应替身。"""

    def __init__(self, status_code: int, payload: Any) -> None:
        """保存响应状态和正文；参数分别对应 HTTP 状态码与 JSON 可序列化对象。"""

        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self) -> Any:
        """返回预置 JSON 正文。"""

        return self._payload


class StubFactorComboAPI:
    """记录登记请求并按顺序返回预置响应。"""

    def __init__(self, responses: list[StubResponse]) -> None:
        """接收登记响应序列并初始化请求记录。"""

        self.responses = list(responses)
        self.register_payloads: list[dict[str, Any]] = []

    def register_report(self, payload: dict[str, Any]) -> StubResponse:
        """记录一次登记请求并返回下一个预置响应。"""

        self.register_payloads.append(deepcopy(payload))
        if not self.responses:
            raise AssertionError("no registration response remains")
        return self.responses.pop(0)


class StubRepository:
    """提供登记资源和刷新计算证据的可控仓储替身。"""

    def __init__(
        self,
        registration: dict[str, Any],
        form: dict[str, Any] | None = None,
        *,
        calculation_runs: list[dict[str, Any]] | None = None,
        calculation_metrics: list[dict[str, Any]] | None = None,
        refresh_validity_snapshots: list[dict[str, Any]] | None = None,
    ) -> None:
        """保存登记资源及可选刷新计算证据；显式空列表表示数据库没有对应证据。"""

        self.registration = {
            "id": 901,
            "combo_id": 701,
            "sub_factor_id": 801,
            "factor_id": None,
            "combo_version_hash": "a" * 64,
            "version_id": 702,
            **registration,
        }
        default_form = {
            "id": 22,
            "session_id": 11,
            "factor_combo_id": self.registration.get("version_id", 702),
            "pipeline_run_id": "combo-22-abcdef0123456789",
            "status": "completed",
        }
        default_form.update(form or {})
        self.form = default_form
        self.version = {
            "id": self.form.get("factor_combo_id"),
            "combo_id": self.registration.get("combo_id"),
            "combo_version_hash": self.registration.get("combo_version_hash"),
            "status": self.form.get("status", "completed"),
        }
        self.form_queries: list[int] = []
        self.calculation_runs = (
            [
                {
                    "factor_id": 801,
                    "is_sub_factor_id": True,
                    "run_id": "ic-refresh-801",
                    "run_status": "completed",
                    "summary_row_count": 4,
                    "populated_metric_row_count": 4,
                    "ic_scope_count": 2,
                }
            ]
            if calculation_runs is None
            else list(calculation_runs)
        )
        self.calculation_metrics = None if calculation_metrics is None else list(calculation_metrics)
        self.refresh_validity_snapshots = (
            [
                {
                    "id": 904,
                    "factor_id": 801,
                    "is_sub_factor_id": True,
                    "run_id": "ic-refresh-801",
                    "time_series_summary_id": 1001,
                    "time_series_summary_run_id": "ic-refresh-801",
                    "time_series_summary_factor_id": 801,
                    "time_series_summary_is_sub_factor_id": True,
                    "cross_sectional_summary_id": 1002,
                    "cross_sectional_summary_run_id": "ic-refresh-801",
                    "cross_sectional_summary_factor_id": 801,
                    "cross_sectional_summary_is_sub_factor_id": True,
                    "time_series_is_valid": True,
                    "cross_sectional_is_valid": None,
                }
            ]
            if refresh_validity_snapshots is None
            else list(refresh_validity_snapshots)
        )
        self.calculation_queries: list[int] = []
        self.refresh_validity_queries: list[tuple[int, int]] = []
        self.registered_sub_factor_queries: list[int] = []

    def get_form(self, form_id: int) -> dict[str, Any] | None:
        """记录表单查询并返回预置快照。"""

        self.form_queries.append(int(form_id))
        if int(form_id) != int(self.form.get("id", 0)):
            return None
        return dict(self.form)

    def get_registration(self, combo_id: int) -> dict[str, Any] | None:
        """返回预置登记记录；参数用于保持仓储接口语义。"""

        if int(combo_id) != int(self.registration["combo_id"]):
            return None
        return dict(self.registration)

    def get_registered_sub_factor(self, sub_factor_id: int) -> dict[str, Any] | None:
        """返回与登记响应一致的数据库子因子记录。"""

        self.registered_sub_factor_queries.append(int(sub_factor_id))
        if int(sub_factor_id) != int(self.registration["sub_factor_id"]):
            return None
        return {"id": int(sub_factor_id), "sub_factor_name": "composite-test-factor", "type": 1}

    def get_registered_factor_detail(self, factor_detail_id: int) -> dict[str, Any] | None:
        """返回登记接口创建的因子详情替身。"""

        if int(factor_detail_id) != 902:
            return None
        return {"id": 902, "factor_id": 801, "is_sub_factor_id": True, "status": 1}

    def get_registered_validity_status(self, validity_status_id: int) -> dict[str, Any] | None:
        """返回登记接口创建的有效性快照替身。"""

        if int(validity_status_id) != 903:
            return None
        return {"id": 903, "factor_id": 801, "is_sub_factor_id": True, "time_series_is_valid": True}

    def get_factor_refresh_calculation_runs(self, sub_factor_id: int) -> list[dict[str, Any]]:
        """记录子因子计算 Run 查询并返回预置的汇总指标证据。"""

        self.calculation_queries.append(int(sub_factor_id))
        return [dict(row) for row in self.calculation_runs]

    def get_factor_refresh_calculation_metrics(self, sub_factor_id: int) -> list[dict[str, Any]]:
        """记录新版计算明细查询，并把预置聚合证据展开成可关联的 summary 行。"""

        self.calculation_queries.append(int(sub_factor_id))
        if self.calculation_metrics is not None:
            return [dict(row) for row in self.calculation_metrics]
        rows: list[dict[str, Any]] = []
        for aggregate in self.calculation_runs:
            count = int(aggregate.get("summary_row_count", 0) or 0)
            populated = int(aggregate.get("populated_metric_row_count", 0) or 0)
            for offset in range(count):
                rows.append(
                    {
                        "id": 1001 + offset,
                        "summary_id": 1001 + offset,
                        "factor_id": aggregate.get("factor_id"),
                        "is_sub_factor_id": aggregate.get("is_sub_factor_id"),
                        "run_id": aggregate.get("run_id"),
                        "run_status": aggregate.get("run_status"),
                        "ic_scope": "time_series" if offset % 2 == 0 else "cross_sectional",
                        "window_scope": "rolling",
                        "mean_ic": 0.1 if offset < populated else None,
                    }
                )
        return rows

    def get_factor_refresh_validity_snapshots(
        self,
        sub_factor_id: int,
        registration_validity_status_id: int,
    ) -> list[dict[str, Any]]:
        """记录刷新有效性查询并返回已排除登记初始快照的预置结果。"""

        self.refresh_validity_queries.append((int(sub_factor_id), int(registration_validity_status_id)))
        return [dict(row) for row in self.refresh_validity_snapshots]

    def get_combo_version(self, version_id: int) -> dict[str, Any] | None:
        """返回与表单指针一致的组合版本快照。"""

        if int(version_id) != int(self.version.get("id", 0)):
            return None
        return dict(self.version)


class StubPerformanceAPI:
    """记录刷新查询且按顺序返回任务状态。"""

    def __init__(self, responses: list[Any]) -> None:
        """接收刷新状态响应序列并初始化查询记录。"""

        self.responses = list(responses)
        self.task_ids: list[str] = []

    def get_refresh_run(self, task_id: str) -> StubResponse:
        """记录任务 ID 并返回下一个预置刷新响应。"""

        self.task_ids.append(task_id)
        if not self.responses:
            raise AssertionError("no refresh response remains")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class StubSubFactorAPI:
    """记录子因子回查参数并返回预置详情。"""

    def __init__(self, response: StubResponse | list[StubResponse]) -> None:
        """保存一个或按顺序排列的子因子详情响应并初始化查询记录。"""

        self.responses = list(response) if isinstance(response, list) else [response]
        self.calls: list[tuple[int, str]] = []

    def get_sub_factor(self, sub_factor_id: int, *, ic_mode: str) -> StubResponse:
        """记录子因子 ID 和 IC 模式，返回预置响应。"""

        self.calls.append((sub_factor_id, ic_mode))
        if not self.responses:
            raise AssertionError("no sub-factor response remains")
        return self.responses.pop(0)


class StubStartConflictAPI:
    """返回启动冲突响应并记录真实 Run 启动请求。"""

    def __init__(self, response: StubResponse) -> None:
        """保存启动接口响应并初始化请求记录。"""

        self.response = response
        self.calls: list[tuple[int, dict[str, Any]]] = []

    def start_run(self, form_id: int, payload: dict[str, Any]) -> StubResponse:
        """记录表单和启动参数，并返回预置响应。"""

        self.calls.append((form_id, deepcopy(payload)))
        return self.response


class StubAgentAPI:
    """返回当前账号唯一可见投研 Agent 的离线替身。"""

    def __init__(self) -> None:
        """初始化用户 ID 记录。"""

        self.user_ids: list[int] = []

    def list_agents(self, user_id: int) -> StubResponse:
        """记录用户 ID 并返回唯一 Agent。"""

        self.user_ids.append(user_id)
        return StubResponse(200, [{"agent_uid": "agent-1", "name": "投研Agent", "enabled": True}])


class StubRealFlowAPI:
    """驱动真实研究流程分支的离线 API 替身。"""

    def __init__(
        self,
        status_responses: list[StubResponse],
        result_responses: list[StubResponse],
        feedback_response: StubResponse | None = None,
    ) -> None:
        """接收按 Run 顺序排列的状态、结果和可选反馈响应。"""

        self.status_responses = list(status_responses)
        self.result_responses = list(result_responses)
        self.feedback_response = feedback_response
        self.start_calls: list[tuple[int, dict[str, Any]]] = []
        self.status_calls: list[tuple[int, str]] = []
        self.result_calls: list[tuple[int, str]] = []
        self.feedback_payloads: list[dict[str, Any]] = []
        self._run_ids = [
            "combo-22-1111111111111111",
            "combo-22-2222222222222222",
            "combo-22-3333333333333333",
        ]

    def start_run(self, form_id: int, payload: dict[str, Any]) -> StubResponse:
        """记录启动参数并返回下一个合法 Run ID。"""

        self.start_calls.append((form_id, deepcopy(payload)))
        run_id = self._run_ids[len(self.start_calls) - 1]
        return StubResponse(
            202,
            {
                "success": True,
                "data": {
                    "form_id": form_id,
                    "pipeline_run_id": run_id,
                    "agent_session_id": f"agent-session-{len(self.start_calls)}",
                    "idempotent_replay": False,
                },
            },
        )

    def get_run_status(self, form_id: int, run_id: str) -> StubResponse:
        """记录状态查询并返回预置状态。"""

        self.status_calls.append((form_id, run_id))
        if not self.status_responses:
            raise AssertionError("no run status response remains")
        response = self.status_responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    def get_run_result(self, form_id: int, run_id: str) -> StubResponse:
        """记录结果查询并返回预置结构化结果。"""

        self.result_calls.append((form_id, run_id))
        if not self.result_responses:
            raise AssertionError("no run result response remains")
        response = self.result_responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    def submit_feedback(self, payload: dict[str, Any]) -> StubResponse:
        """记录真实反馈请求并返回预置反馈响应。"""

        self.feedback_payloads.append(deepcopy(payload))
        if self.feedback_response is None:
            raise AssertionError("feedback response was not configured")
        return self.feedback_response


def _settings() -> FactorComboSettings:
    """构造不执行等待的离线组合流程配置。"""

    return FactorComboSettings(
        agent_uid=None,
        poll_interval_seconds=0,
        poll_timeout_seconds=1,
        max_research_rounds=2,
        worker_contracts_enabled=False,
        cleanup_test_data=False,
        agent_base_url="https://agent.example.test/api/v2",
        refresh_poll_interval_seconds=0,
        refresh_poll_timeout_seconds=1,
        max_refresh_polls=5,
        max_technical_retries=2,
    )


def _service(
    factor_api: StubFactorComboAPI,
    performance_api: StubPerformanceAPI,
    sub_factor_api: StubSubFactorAPI,
    *,
    repository: StubRepository | None = None,
) -> FactorComboService:
    """构造只注入离线替身的组合流程服务。"""

    registration = {"id": 901, "combo_id": 701, "sub_factor_id": 801}
    return FactorComboService(
        chat_api=None,  # type: ignore[arg-type]
        factor_combo_api=factor_api,  # type: ignore[arg-type]
        repository=repository or StubRepository(registration),  # type: ignore[arg-type]
        settings=_settings(),
        scope=ResourceScope(),
        performance_api=performance_api,  # type: ignore[arg-type]
        sub_factor_api=sub_factor_api,  # type: ignore[arg-type]
    )


def _real_flow_service(
    factor_api: StubRealFlowAPI,
    *,
    max_rounds: int = 2,
    max_technical_retries: int = 1,
    poll_timeout_seconds: float = 1,
) -> FactorComboService:
    """构造注入 Agent 和真实流程替身的离线服务。"""

    return FactorComboService(
        chat_api=None,  # type: ignore[arg-type]
        factor_combo_api=factor_api,  # type: ignore[arg-type]
        repository=StubRepository({"id": 901, "combo_id": 701, "sub_factor_id": 801}),  # type: ignore[arg-type]
        settings=replace(
            _settings(),
            max_research_rounds=max_rounds,
            max_technical_retries=max_technical_retries,
            poll_timeout_seconds=poll_timeout_seconds,
        ),
        scope=ResourceScope(),
        agent_api=StubAgentAPI(),  # type: ignore[arg-type]
    )


def _run_status_response(run_id: str, status: str, action: str) -> StubResponse:
    """构造带完整运行归属字段的状态响应。"""

    return StubResponse(
        200,
        {
            "success": True,
            "data": {
                "form_id": 22,
                "pipeline_run_id": run_id,
                "pipeline_status": status,
                "recommended_action": action,
            },
        },
    )


def _run_result_response(run_id: str, *, valid: bool, continue_exploration: bool) -> StubResponse:
    """构造符合真实结果契约的有效或无效报告。"""

    return StubResponse(
        200,
        {
            "success": True,
            "data": {
                "form_id": 22,
                "pipeline_run_id": run_id,
                "pipeline_status": "completed",
                "result": {
                    "factor_combo_report": {"factor_name": "composite-test-factor"},
                    "factor_combo_review": {
                        "experiment_valid": valid,
                        "registration_ready": valid,
                        "search": {"continue_exploration_available": continue_exploration},
                    },
                    "factor_validity_status": {
                        "time_series_is_valid": valid,
                        "cross_sectional_is_valid": None,
                    },
                },
            },
        },
    )


def _result() -> RealPipelineResult:
    """构造不含伪造指标的最小真实结果对象。"""

    form = SubmittedForm(session_id=11, form_id=22, pool_id=33, status="completed")
    run = RealRun(form=form, pipeline_run_id="combo-22-abcdef0123456789")
    return RealPipelineResult(
        run=run,
        report={"factor_name": "composite-test-factor", "components": []},
        review={"experiment_valid": True, "registration_ready": True},
        validity={"time_series_is_valid": True, "cross_sectional_is_valid": None},
        raw_data={"pipeline_run_id": run.pipeline_run_id},
    )


def _registration_response(
    *,
    replay: bool,
    task_id: Any = "refresh-701",
    refresh_status: str = "queued",
    refresh_submit_error: str = "",
) -> StubResponse:
    """构造登记首次或幂等重放成功响应。"""

    return StubResponse(
        200 if replay else 201,
        {
            "success": True,
            "data": {
                "registered": True,
                "idempotent_replay": replay,
                "factor_combo_version_id": 702,
                "combo_id": 701,
                "combo_version_hash": "a" * 64,
                "sub_factor_id": 801,
                "factor_detail_id": 902,
                "factor_validity_status_id": 903,
                "registration_id": 901,
                "sub_factor_type": 1,
                "refresh_task_id": task_id,
                "refresh_status": refresh_status,
                "refresh_submit_error": refresh_submit_error,
                "sub_factor": {"id": 801, "sub_factor_name": "composite-test-factor"},
                "factor_detail": {"id": 902, "factor_id": 801, "is_sub_factor_id": True},
                "factor_validity_status": {"id": 903, "factor_id": 801, "is_sub_factor_id": True},
                "registration": {
                    "id": 901,
                    "combo_id": 701,
                    "combo_version_hash": "a" * 64,
                    "sub_factor_id": 801,
                },
            },
        },
    )


def _completed_refresh_response(task_id: str = "refresh-701") -> StubResponse:
    """构造所有刷新单元均完成的任务响应。"""

    return StubResponse(
        200,
        {
            "success": True,
            "data": {
                "task_id": task_id,
                "status": "completed",
                "completed_factors": ["composite-test-factor"],
                "incomplete_factors": [],
                "summary": {
                    "total_units": 4,
                    "completed_units": 4,
                    "skipped_window_count": 0,
                    "failed_unit_count": 0,
                    "not_run_unit_count": 0,
                },
                "results": [
                    {
                        "factor_name": "composite-test-factor",
                        "run_id": "ic-refresh-801",
                    }
                ],
            },
        },
    )


def _active_refresh_response(status: str = "running", task_id: str = "refresh-701") -> StubResponse:
    """构造仍在执行中的刷新任务响应。"""

    return StubResponse(
        200,
        {
            "success": True,
            "data": {
                "task_id": task_id,
                "status": status,
            },
        },
    )


class TestRealRegistrationRefreshFlow:
    """验证登记接口之后的刷新闭环和严格失败分类。"""

    def test_completed_registration_conflict_queries_existing_mapping_without_reverse_action(self) -> None:
        """登记返回“已完成”冲突时查询现有映射，并且不重复登记或创建刷新任务。"""

        conflict = StubResponse(
            409,
            {
                "success": False,
                "error": "factor combo registration already completed",
            },
        )
        factor_api = StubFactorComboAPI([conflict])
        repository = StubRepository(
            {"id": 901, "combo_id": 701, "version_id": 702, "sub_factor_id": 801},
            {
                "id": 22,
                "factor_combo_id": 702,
                "pipeline_run_id": "combo-22-abcdef0123456789",
                "status": "completed",
            },
        )
        service = FactorComboService(
            chat_api=None,  # type: ignore[arg-type]
            factor_combo_api=factor_api,  # type: ignore[arg-type]
            repository=repository,  # type: ignore[arg-type]
            settings=_settings(),
            scope=ResourceScope(),
            performance_api=StubPerformanceAPI([]),  # type: ignore[arg-type]
            sub_factor_api=StubSubFactorAPI(StubResponse(200, {"success": True, "data": {}})),  # type: ignore[arg-type]
        )

        with pytest.raises(FactorComboFlowError) as error:
            service.register_real_result_and_refresh(_result())

        assert error.value.outcome == FlowOutcome.FAIL_CONTRACT, error.value
        assert len(factor_api.register_payloads) == 1, factor_api.register_payloads
        assert repository.form_queries == [22], repository.form_queries
        assert error.value.details["existing_registration"]["version"]["combo_id"] == 701, error.value.details
        assert error.value.details["existing_registration"]["registration"]["id"] == 901, error.value.details
        assert "do_not_create_another_registration" in str(error.value), str(error.value)

    def test_registration_replay_refresh_and_sub_factor_query_are_all_executed(self) -> None:
        """验证同一登记请求实际发送两次，并继续查询刷新任务和子因子。"""

        factor_api = StubFactorComboAPI([_registration_response(replay=False), _registration_response(replay=True)])
        performance_api = StubPerformanceAPI([_completed_refresh_response()])
        sub_factor_api = StubSubFactorAPI(
            StubResponse(
                200,
                {
                    "success": True,
                    "data": {
                        "id": 801,
                        "sub_factor_name": "composite-test-factor",
                        "factor_ic_summary_metrics": [{"window_scope": "rolling", "mean_ic": 0.1}],
                    },
                },
            )
        )
        result = _service(factor_api, performance_api, sub_factor_api).register_real_result_and_refresh(_result())

        assert result.outcome == FlowOutcome.PASS_REGISTERED, result
        assert len(factor_api.register_payloads) == 2, factor_api.register_payloads
        assert factor_api.register_payloads[0] == factor_api.register_payloads[1], factor_api.register_payloads
        assert performance_api.task_ids == ["refresh-701"], performance_api.task_ids
        assert sub_factor_api.calls == [(801, "timeseries")], sub_factor_api.calls
        assert result.database_sub_factor == {
            "id": 801,
            "sub_factor_name": "composite-test-factor",
            "type": 1,
        }, result
        assert result.database_refresh.calculation_runs[0]["run_id"] == "ic-refresh-801", result.database_refresh
        assert result.database_refresh.validity_snapshots[0]["id"] == 904, result.database_refresh
        assert result.database_refresh.matched_run_ids == ("ic-refresh-801",), result.database_refresh

    def test_database_sub_factor_is_queried_again_after_refresh(self) -> None:
        """登记前后的数据库子因子读取必须各执行一次，最终结果使用刷新后的快照。"""

        repository = StubRepository({"id": 901, "combo_id": 701, "sub_factor_id": 801})
        factor_api = StubFactorComboAPI([_registration_response(replay=False), _registration_response(replay=True)])
        performance_api = StubPerformanceAPI([_completed_refresh_response()])
        sub_factor_api = StubSubFactorAPI(
            StubResponse(
                200,
                {
                    "success": True,
                    "data": {
                        "id": 801,
                        "sub_factor_name": "composite-test-factor",
                        "factor_ic_summary_metrics": [{"mean_ic": 0.1}],
                    },
                },
            )
        )

        result = _service(
            factor_api,
            performance_api,
            sub_factor_api,
            repository=repository,
        ).register_real_result_and_refresh(_result())

        assert repository.registered_sub_factor_queries == [801, 801], repository.registered_sub_factor_queries
        assert result.database_sub_factor["id"] == 801, result.database_sub_factor

    def test_registered_flow_requires_refresh_calculation_rows_in_database(self) -> None:
        """刷新 API 已完成但数据库没有汇总指标时必须判定刷新失败。"""

        service = FactorComboService(
            chat_api=None,  # type: ignore[arg-type]
            factor_combo_api=StubFactorComboAPI(
                [_registration_response(replay=False), _registration_response(replay=True)]
            ),  # type: ignore[arg-type]
            repository=StubRepository(
                {"id": 901, "combo_id": 701, "sub_factor_id": 801},
                calculation_runs=[],
            ),  # type: ignore[arg-type]
            settings=_settings(),
            scope=ResourceScope(),
            performance_api=StubPerformanceAPI([_completed_refresh_response()]),  # type: ignore[arg-type]
            sub_factor_api=StubSubFactorAPI(
                StubResponse(
                    200,
                    {
                        "success": True,
                        "data": {
                            "id": 801,
                            "sub_factor_name": "composite-test-factor",
                            "factor_ic_summary_metrics": [{"ic": 0.1}],
                        },
                    },
                )
            ),  # type: ignore[arg-type]
        )

        with pytest.raises(FactorComboFlowError) as error:
            service.register_real_result_and_refresh(_result())

        assert error.value.outcome == FlowOutcome.FAIL_REFRESH, error.value
        assert "factor_ic_summary_metrics" in str(error.value), str(error.value)

    def test_registered_flow_rejects_summary_rows_with_only_null_metrics(self) -> None:
        """数据库只有汇总占位行且所有指标为空时不得判定计算完成。"""

        service = FactorComboService(
            chat_api=None,  # type: ignore[arg-type]
            factor_combo_api=StubFactorComboAPI(
                [_registration_response(replay=False), _registration_response(replay=True)]
            ),  # type: ignore[arg-type]
            repository=StubRepository(
                {"id": 901, "combo_id": 701, "sub_factor_id": 801},
                calculation_runs=[
                    {
                        "factor_id": 801,
                        "is_sub_factor_id": True,
                        "run_id": "ic-refresh-801",
                        "run_status": "completed",
                        "summary_row_count": 4,
                        "populated_metric_row_count": 0,
                        "ic_scope_count": 2,
                    }
                ],
            ),  # type: ignore[arg-type]
            settings=_settings(),
            scope=ResourceScope(),
            performance_api=StubPerformanceAPI([_completed_refresh_response()]),  # type: ignore[arg-type]
            sub_factor_api=StubSubFactorAPI(
                StubResponse(
                    200,
                    {
                        "success": True,
                        "data": {
                            "id": 801,
                            "sub_factor_name": "composite-test-factor",
                            "factor_ic_summary_metrics": [{"ic": 0.1}],
                        },
                    },
                )
            ),  # type: ignore[arg-type]
        )

        with pytest.raises(FactorComboFlowError) as error:
            service.register_real_result_and_refresh(_result())

        assert error.value.outcome == FlowOutcome.FAIL_REFRESH, error.value
        assert "non-null" in str(error.value), str(error.value)

    def test_registered_flow_requires_refresh_validity_snapshot_linked_to_summary(self) -> None:
        """数据库缺少引用计算汇总的刷新有效性快照时不得通过登记闭环。"""

        service = FactorComboService(
            chat_api=None,  # type: ignore[arg-type]
            factor_combo_api=StubFactorComboAPI(
                [_registration_response(replay=False), _registration_response(replay=True)]
            ),  # type: ignore[arg-type]
            repository=StubRepository(
                {"id": 901, "combo_id": 701, "sub_factor_id": 801},
                refresh_validity_snapshots=[],
            ),  # type: ignore[arg-type]
            settings=_settings(),
            scope=ResourceScope(),
            performance_api=StubPerformanceAPI([_completed_refresh_response()]),  # type: ignore[arg-type]
            sub_factor_api=StubSubFactorAPI(
                StubResponse(
                    200,
                    {
                        "success": True,
                        "data": {
                            "id": 801,
                            "sub_factor_name": "composite-test-factor",
                            "factor_ic_summary_metrics": [{"ic": 0.1}],
                        },
                    },
                )
            ),  # type: ignore[arg-type]
        )

        with pytest.raises(FactorComboFlowError) as error:
            service.register_real_result_and_refresh(_result())

        assert error.value.outcome == FlowOutcome.FAIL_REFRESH, error.value
        assert "factor_validity_status" in str(error.value), str(error.value)

    def test_registered_flow_accepts_in_place_update_of_registration_validity_snapshot(self) -> None:
        """刷新原地更新登记快照、保留 factor_combo_register Run 前缀时仍应按 summary 外键验收。"""

        repository = StubRepository(
            {"id": 901, "combo_id": 701, "sub_factor_id": 801},
            refresh_validity_snapshots=[
                {
                    "id": 903,
                    "factor_id": 801,
                    "is_sub_factor_id": True,
                    "run_id": "factor_combo_register:" + "a" * 64,
                    "time_series_summary_id": 1001,
                    "time_series_summary_run_id": "ic-refresh-801",
                    "time_series_summary_factor_id": 801,
                    "time_series_summary_is_sub_factor_id": True,
                    "cross_sectional_summary_id": 1002,
                    "cross_sectional_summary_run_id": "ic-refresh-801",
                    "cross_sectional_summary_factor_id": 801,
                    "cross_sectional_summary_is_sub_factor_id": True,
                }
            ],
        )
        service = FactorComboService(
            chat_api=None,  # type: ignore[arg-type]
            factor_combo_api=StubFactorComboAPI(
                [_registration_response(replay=False), _registration_response(replay=True)]
            ),  # type: ignore[arg-type]
            repository=repository,  # type: ignore[arg-type]
            settings=_settings(),
            scope=ResourceScope(),
            performance_api=StubPerformanceAPI([_completed_refresh_response()]),  # type: ignore[arg-type]
            sub_factor_api=StubSubFactorAPI(
                StubResponse(
                    200,
                    {
                        "success": True,
                        "data": {
                            "id": 801,
                            "sub_factor_name": "composite-test-factor",
                            "factor_ic_summary_metrics": [{"ic": 0.1}],
                        },
                    },
                )
            ),  # type: ignore[arg-type]
        )

        result = service.register_real_result_and_refresh(_result())

        assert result.outcome == FlowOutcome.PASS_REGISTERED, result
        assert result.database_refresh.validity_snapshots[0]["run_id"].startswith(
            "factor_combo_register:"
        ), result.database_refresh

    def test_registered_flow_ignores_historical_failed_run_not_linked_to_latest_refresh(self) -> None:
        """同一子因子存在历史失败 Run 时，只要当前刷新快照引用的新 Run 完成，不应被历史行污染。"""

        repository = StubRepository(
            {"id": 901, "combo_id": 701, "sub_factor_id": 801},
            calculation_runs=[
                {
                    "factor_id": 801,
                    "is_sub_factor_id": True,
                    "run_id": "ic-old-failed",
                    "run_status": "failed",
                    "summary_row_count": 4,
                    "populated_metric_row_count": 0,
                    "ic_scope_count": 2,
                },
                {
                    "factor_id": 801,
                    "is_sub_factor_id": True,
                    "run_id": "ic-refresh-801",
                    "run_status": "completed",
                    "summary_row_count": 4,
                    "populated_metric_row_count": 4,
                    "ic_scope_count": 2,
                },
            ],
        )
        service = FactorComboService(
            chat_api=None,  # type: ignore[arg-type]
            factor_combo_api=StubFactorComboAPI(
                [_registration_response(replay=False), _registration_response(replay=True)]
            ),  # type: ignore[arg-type]
            repository=repository,  # type: ignore[arg-type]
            settings=_settings(),
            scope=ResourceScope(),
            performance_api=StubPerformanceAPI([_completed_refresh_response()]),  # type: ignore[arg-type]
            sub_factor_api=StubSubFactorAPI(
                StubResponse(
                    200,
                    {
                        "success": True,
                        "data": {
                            "id": 801,
                            "sub_factor_name": "composite-test-factor",
                            "factor_ic_summary_metrics": [{"ic": 0.1}],
                        },
                    },
                )
            ),  # type: ignore[arg-type]
        )

        result = service.register_real_result_and_refresh(_result())

        assert [row["run_id"] for row in result.database_refresh.calculation_runs] == ["ic-refresh-801"], result

    def test_api_metric_and_database_summary_mismatch_is_a_contract_failure(self) -> None:
        """详情接口明确返回的 summary 指标与同一 DB summary 不一致时必须失败。"""

        service = _service(
            StubFactorComboAPI([_registration_response(replay=False), _registration_response(replay=True)]),
            StubPerformanceAPI([_completed_refresh_response()]),
            StubSubFactorAPI(
                StubResponse(
                    200,
                    {
                        "success": True,
                        "data": {
                            "id": 801,
                            "sub_factor_name": "composite-test-factor",
                            "factor_ic_summary_metrics": [{"id": 1001, "mean_ic": 0.2}],
                        },
                    },
                )
            ),
        )

        with pytest.raises(FactorComboFlowError) as error:
            service.register_real_result_and_refresh(_result())

        assert error.value.outcome == FlowOutcome.FAIL_CONTRACT, error.value
        assert "mean_ic" in str(error.value), str(error.value)

    def test_api_validity_and_database_snapshot_mismatch_is_a_contract_failure(self) -> None:
        """详情接口明确返回的有效性结论与同一 DB 快照不一致时必须失败。"""

        repository = StubRepository(
            {"id": 901, "combo_id": 701, "sub_factor_id": 801},
            refresh_validity_snapshots=[
                {
                    "id": 904,
                    "factor_id": 801,
                    "is_sub_factor_id": True,
                    "run_id": "ic-refresh-801",
                    "time_series_summary_id": 1001,
                    "time_series_summary_run_id": "ic-refresh-801",
                    "time_series_summary_factor_id": 801,
                    "time_series_summary_is_sub_factor_id": True,
                    "time_series_is_valid": True,
                }
            ],
        )
        service = FactorComboService(
            chat_api=None,  # type: ignore[arg-type]
            factor_combo_api=StubFactorComboAPI(
                [_registration_response(replay=False), _registration_response(replay=True)]
            ),  # type: ignore[arg-type]
            repository=repository,  # type: ignore[arg-type]
            settings=_settings(),
            scope=ResourceScope(),
            performance_api=StubPerformanceAPI([_completed_refresh_response()]),  # type: ignore[arg-type]
            sub_factor_api=StubSubFactorAPI(
                StubResponse(
                    200,
                    {
                        "success": True,
                        "data": {
                            "id": 801,
                            "sub_factor_name": "composite-test-factor",
                            "factor_ic_summary_metrics": [{"id": 1001, "mean_ic": 0.1}],
                            "factor_validity_status": {"id": 904, "time_series_is_valid": False},
                        },
                    },
                )
            ),  # type: ignore[arg-type]
        )

        with pytest.raises(FactorComboFlowError) as error:
            service.register_real_result_and_refresh(_result())

        assert error.value.outcome == FlowOutcome.FAIL_CONTRACT, error.value
        assert "time_series_is_valid" in str(error.value), str(error.value)

    def test_registered_flow_rejects_database_calculation_for_another_factor(self) -> None:
        """数据库汇总指标属于其他因子时必须报告数据关联契约失败。"""

        service = FactorComboService(
            chat_api=None,  # type: ignore[arg-type]
            factor_combo_api=StubFactorComboAPI(
                [_registration_response(replay=False), _registration_response(replay=True)]
            ),  # type: ignore[arg-type]
            repository=StubRepository(
                {"id": 901, "combo_id": 701, "sub_factor_id": 801},
                calculation_runs=[
                    {
                        "factor_id": 999,
                        "is_sub_factor_id": True,
                        "run_id": "ic-refresh-801",
                        "run_status": "completed",
                        "summary_row_count": 4,
                        "populated_metric_row_count": 4,
                        "ic_scope_count": 2,
                    }
                ],
            ),  # type: ignore[arg-type]
            settings=_settings(),
            scope=ResourceScope(),
            performance_api=StubPerformanceAPI([_completed_refresh_response()]),  # type: ignore[arg-type]
            sub_factor_api=StubSubFactorAPI(
                StubResponse(
                    200,
                    {
                        "success": True,
                        "data": {
                            "id": 801,
                            "sub_factor_name": "composite-test-factor",
                            "factor_ic_summary_metrics": [{"ic": 0.1}],
                        },
                    },
                )
            ),  # type: ignore[arg-type]
        )

        with pytest.raises(FactorComboFlowError) as error:
            service.register_real_result_and_refresh(_result())

        assert error.value.outcome == FlowOutcome.FAIL_CONTRACT, error.value
        assert "factor_id" in str(error.value), str(error.value)

    def test_registered_flow_rejects_refresh_run_id_not_found_in_database(self) -> None:
        """刷新响应明确给出计算 Run ID 但数据库未产生该 Run 时不得通过。"""

        refresh = _completed_refresh_response().json()
        refresh["data"]["results"][0]["run_id"] = "ic-refresh-missing"
        service = FactorComboService(
            chat_api=None,  # type: ignore[arg-type]
            factor_combo_api=StubFactorComboAPI(
                [_registration_response(replay=False), _registration_response(replay=True)]
            ),  # type: ignore[arg-type]
            repository=StubRepository(
                {"id": 901, "combo_id": 701, "sub_factor_id": 801}
            ),  # type: ignore[arg-type]
            settings=_settings(),
            scope=ResourceScope(),
            performance_api=StubPerformanceAPI([StubResponse(200, refresh)]),  # type: ignore[arg-type]
            sub_factor_api=StubSubFactorAPI(
                StubResponse(
                    200,
                    {
                        "success": True,
                        "data": {
                            "id": 801,
                            "sub_factor_name": "composite-test-factor",
                            "factor_ic_summary_metrics": [{"ic": 0.1}],
                        },
                    },
                )
            ),  # type: ignore[arg-type]
        )

        with pytest.raises(FactorComboFlowError) as error:
            service.register_real_result_and_refresh(_result())

        assert error.value.outcome == FlowOutcome.FAIL_REFRESH, error.value
        assert "run_id" in str(error.value), str(error.value)

    def test_registration_status_and_replay_marker_must_be_consistent(self) -> None:
        """登记返回 200 但声称不是幂等重放时，不得继续刷新验收。"""

        body = _registration_response(replay=False).json()
        factor_api = StubFactorComboAPI([StubResponse(200, body)])
        performance_api = StubPerformanceAPI([])
        service = _service(
            factor_api,
            performance_api,
            StubSubFactorAPI(StubResponse(200, {"success": True, "data": {}})),
        )

        with pytest.raises(FactorComboFlowError) as error:
            service.register_real_result_and_refresh(_result())

        assert error.value.outcome == FlowOutcome.FAIL_CONTRACT, error.value
        assert performance_api.task_ids == [], performance_api.task_ids

    def test_registration_can_recover_from_lost_created_response_and_still_replay(self) -> None:
        """首次 201 响应丢失后以 200 幂等结果恢复，并继续执行显式重放、刷新和详情回查。"""

        recovered_body = _registration_response(replay=True).json()
        for field_name in ("sub_factor", "factor_detail", "factor_validity_status", "registration"):
            recovered_body["data"][field_name] = {}
        factor_api = StubFactorComboAPI(
            [
                StubResponse(200, recovered_body),
                _registration_response(replay=True),
            ]
        )
        performance_api = StubPerformanceAPI([_completed_refresh_response()])
        sub_factor_api = StubSubFactorAPI(
            StubResponse(
                200,
                {
                    "success": True,
                    "data": {
                        "id": 801,
                        "sub_factor_name": "composite-test-factor",
                        "factor_validity_status": {"time_series_is_valid": True},
                    },
                },
            )
        )

        result = _service(factor_api, performance_api, sub_factor_api).register_real_result_and_refresh(_result())

        assert result.outcome == FlowOutcome.PASS_REGISTERED, result
        assert result.first_registration["idempotent_replay"] is True, result.first_registration
        assert len(factor_api.register_payloads) == 2, factor_api.register_payloads
        assert performance_api.task_ids == ["refresh-701"], performance_api.task_ids

    def test_registration_factor_name_must_match_pipeline_report(self) -> None:
        """登记接口和数据库即使彼此自洽，也不能把与 Pipeline 报告不同的因子名判为通过。"""

        body = _registration_response(replay=False).json()
        body["data"]["sub_factor"]["sub_factor_name"] = "different-factor-name"
        factor_api = StubFactorComboAPI([StubResponse(201, body)])
        performance_api = StubPerformanceAPI([])
        service = _service(
            factor_api,
            performance_api,
            StubSubFactorAPI(StubResponse(200, {"success": True, "data": {}})),
        )

        with pytest.raises(FactorComboFlowError) as error:
            service.register_real_result_and_refresh(_result())

        assert error.value.outcome == FlowOutcome.FAIL_CONTRACT, error.value
        assert performance_api.task_ids == [], performance_api.task_ids

    def test_first_registration_requires_all_persisted_resource_objects(self) -> None:
        """首次登记缺少因子详情、有效性快照或登记对象时不得被当作完整登记。"""

        body = _registration_response(replay=False).json()
        del body["data"]["factor_validity_status"]
        factor_api = StubFactorComboAPI([StubResponse(201, body)])
        performance_api = StubPerformanceAPI([])
        service = _service(
            factor_api,
            performance_api,
            StubSubFactorAPI(StubResponse(200, {"success": True, "data": {}})),
        )

        with pytest.raises(FactorComboFlowError) as error:
            service.register_real_result_and_refresh(_result())

        assert error.value.outcome == FlowOutcome.FAIL_CONTRACT, error.value
        assert performance_api.task_ids == [], performance_api.task_ids

    def test_registration_nested_identity_must_match_top_level_ids(self) -> None:
        """登记响应嵌套资源 ID 与顶层 ID 不一致时必须阻止后续刷新。"""

        body = _registration_response(replay=False).json()
        body["data"]["factor_detail"]["id"] = 999
        factor_api = StubFactorComboAPI([StubResponse(201, body)])
        performance_api = StubPerformanceAPI([])
        service = _service(
            factor_api,
            performance_api,
            StubSubFactorAPI(StubResponse(200, {"success": True, "data": {}})),
        )

        with pytest.raises(FactorComboFlowError) as error:
            service.register_real_result_and_refresh(_result())

        assert error.value.outcome == FlowOutcome.FAIL_CONTRACT, error.value
        assert performance_api.task_ids == [], performance_api.task_ids

    def test_numeric_refresh_task_id_is_normalized_for_get_query(self) -> None:
        """登记接口返回数字刷新任务 ID 时统一转为路径字符串并继续完成验收。"""

        factor_api = StubFactorComboAPI(
            [
                _registration_response(replay=False, task_id=701),
                _registration_response(replay=True, task_id=701),
            ]
        )
        performance_api = StubPerformanceAPI([_completed_refresh_response(task_id="701")])
        sub_factor_api = StubSubFactorAPI(
            StubResponse(
                200,
                {
                    "success": True,
                    "data": {
                        "id": 801,
                        "sub_factor_name": "composite-test-factor",
                        "factor_ic_summary_metrics": [{"ic": 0.1}],
                    },
                },
            )
        )

        result = _service(factor_api, performance_api, sub_factor_api).register_real_result_and_refresh(_result())

        assert result.refresh.task_id == "701", result.refresh
        assert performance_api.task_ids == ["701"], performance_api.task_ids

    def test_completed_refresh_with_problem_units_is_not_passed(self) -> None:
        """顶层 completed 但存在失败单元时必须分类为刷新失败。"""

        bad_refresh = _completed_refresh_response().json()
        bad_refresh["data"]["summary"]["failed_unit_count"] = 1
        performance_api = StubPerformanceAPI([StubResponse(200, bad_refresh)])
        service = _service(
            StubFactorComboAPI([]),
            performance_api,
            StubSubFactorAPI(StubResponse(200, {"success": True, "data": {}})),
        )

        with pytest.raises(FactorComboFlowError) as error:
            service.poll_performance_refresh("refresh-701", "composite-test-factor")

        assert error.value.outcome == FlowOutcome.FAIL_REFRESH, error.value

    def test_refresh_summary_inconsistency_is_a_contract_failure(self) -> None:
        """服务端返回不一致的派生计数时必须分类为契约失败。"""

        payload = _completed_refresh_response().json()
        payload["data"]["summary"]["problem_unit_count"] = 9
        performance_api = StubPerformanceAPI([StubResponse(200, payload)])
        service = _service(
            StubFactorComboAPI([]),
            performance_api,
            StubSubFactorAPI(StubResponse(200, {"success": True, "data": {}})),
        )

        with pytest.raises(FactorComboFlowError) as error:
            service.poll_performance_refresh("refresh-701", "composite-test-factor")

        assert error.value.outcome == FlowOutcome.FAIL_CONTRACT, error.value

    def test_missing_refresh_task_id_is_a_contract_failure(self) -> None:
        """登记成功但缺少刷新任务 ID 时不得继续或伪造通过。"""

        response = _registration_response(replay=False).json()
        del response["data"]["refresh_task_id"]
        factor_api = StubFactorComboAPI([StubResponse(201, response)])
        service = _service(
            factor_api,
            StubPerformanceAPI([]),
            StubSubFactorAPI(StubResponse(200, {"success": True, "data": {}})),
        )

        with pytest.raises(FactorComboFlowError) as error:
            service.register_real_result_and_refresh(_result())

        assert error.value.outcome == FlowOutcome.FAIL_CONTRACT, error.value
        assert len(factor_api.register_payloads) == 1, factor_api.register_payloads

    @pytest.mark.parametrize("refresh_status", ["not_configured", "submit_failed"])
    def test_refresh_submission_failure_is_classified_as_refresh_failure(self, refresh_status: str) -> None:
        """登记成功但后端提交刷新任务失败时保留刷新失败分类，不误报为契约错误。"""

        factor_api = StubFactorComboAPI(
            [
                _registration_response(
                    replay=False,
                    refresh_status=refresh_status,
                    refresh_submit_error="backtest unavailable" if refresh_status == "submit_failed" else "",
                )
            ]
        )
        service = _service(
            factor_api,
            StubPerformanceAPI([]),
            StubSubFactorAPI(StubResponse(200, {"success": True, "data": {}})),
        )

        with pytest.raises(FactorComboFlowError) as error:
            service.register_real_result_and_refresh(_result())

        assert error.value.outcome == FlowOutcome.FAIL_REFRESH, error.value

    def test_malformed_database_registration_is_a_contract_failure_not_type_error(self) -> None:
        """数据库登记记录字段类型异常时返回明确契约分类，而不是泄露底层 TypeError/ValueError。"""

        factor_api = StubFactorComboAPI([_registration_response(replay=False)])
        service = FactorComboService(
            chat_api=None,  # type: ignore[arg-type]
            factor_combo_api=factor_api,  # type: ignore[arg-type]
            repository=StubRepository(
                {"id": "not-an-int", "combo_id": 701, "sub_factor_id": 801}
            ),  # type: ignore[arg-type]
            settings=_settings(),
            scope=ResourceScope(),
            performance_api=StubPerformanceAPI([]),  # type: ignore[arg-type]
            sub_factor_api=StubSubFactorAPI(StubResponse(200, {"success": True, "data": {}})),  # type: ignore[arg-type]
        )

        with pytest.raises(FactorComboFlowError) as error:
            service.register_real_result_and_refresh(_result())

        assert error.value.outcome == FlowOutcome.FAIL_CONTRACT, error.value

    def test_registration_replay_missing_identity_is_a_contract_failure(self) -> None:
        """登记重放缺少资源 ID 时不得继续查询刷新任务或误判为幂等成功。"""

        replay = _registration_response(replay=True).json()
        del replay["data"]["registration_id"]
        factor_api = StubFactorComboAPI(
            [
                _registration_response(replay=False),
                StubResponse(200, replay),
            ]
        )
        performance_api = StubPerformanceAPI([])
        service = _service(
            factor_api,
            performance_api,
            StubSubFactorAPI(StubResponse(200, {"success": True, "data": {}})),
        )

        with pytest.raises(FactorComboFlowError) as error:
            service.register_real_result_and_refresh(_result())

        assert error.value.outcome == FlowOutcome.FAIL_CONTRACT, error.value
        assert performance_api.task_ids == [], performance_api.task_ids
        assert "registration_id" in str(error.value), str(error.value)

    def test_registration_replay_normalizes_numeric_identity_types(self) -> None:
        """登记重放以字符串返回资源 ID 时，按业务 ID 比较而不是误报幂等冲突。"""

        replay = _registration_response(replay=True).json()
        for field_name in ("sub_factor_id", "registration_id", "combo_id"):
            replay["data"][field_name] = str(replay["data"][field_name])
        factor_api = StubFactorComboAPI(
            [
                _registration_response(replay=False),
                StubResponse(200, replay),
            ]
        )
        performance_api = StubPerformanceAPI([_completed_refresh_response()])
        sub_factor_api = StubSubFactorAPI(
            StubResponse(
                200,
                {
                    "success": True,
                    "data": {
                        "id": 801,
                        "sub_factor_name": "composite-test-factor",
                        "factor_ic_summary_metrics": [{"ic": 0.11}],
                    },
                },
            )
        )

        result = _service(factor_api, performance_api, sub_factor_api).register_real_result_and_refresh(_result())

        assert result.outcome == FlowOutcome.PASS_REGISTERED, result

    def test_all_null_refresh_metrics_are_not_evidence(self) -> None:
        """子因子详情只返回指标字段但全部为 null 时必须判定刷新证据缺失。"""

        detail = {
            "success": True,
            "data": {
                "id": 801,
                "sub_factor_name": "composite-test-factor",
                    "factor_ic_summary_metric": {
                    "id": 1001,
                    "ic": None,
                    "icir": None,
                    "score": None,
                    "time_series_is_valid": None,
                    "overall_status": "unknown",
                },
            },
        }
        service = _service(
            StubFactorComboAPI([]),
            StubPerformanceAPI([]),
            StubSubFactorAPI(StubResponse(200, detail)),
        )

        with pytest.raises(FactorComboFlowError) as error:
            service.verify_registered_sub_factor(801, "composite-test-factor")

        assert error.value.outcome == FlowOutcome.FAIL_REFRESH, error.value

    def test_top_level_report_score_is_not_refresh_evidence(self) -> None:
        """详情顶层普通报告 score 不能替代指标容器中的刷新计算证据。"""

        detail = StubResponse(
            200,
            {
                "success": True,
                "data": {
                    "id": 801,
                    "sub_factor_name": "composite-test-factor",
                    "score": 99,
                    "metadata": {"report": {"score": 88}},
                },
            },
        )
        service = _service(StubFactorComboAPI([]), StubPerformanceAPI([]), StubSubFactorAPI(detail))

        with pytest.raises(FactorComboFlowError) as error:
            service.verify_registered_sub_factor(801, "composite-test-factor")

        assert error.value.outcome == FlowOutcome.FAIL_REFRESH, error.value

    def test_false_validity_flag_is_valid_refresh_evidence(self) -> None:
        """刷新后的 invalid 布尔结论也是有效证据，不能因值为 False 被当成缺失。"""

        detail = StubResponse(
            200,
            {
                "success": True,
                "data": {
                    "id": 801,
                    "sub_factor_name": "composite-test-factor",
                    "factor_ic_summary_metric": {"time_series_is_valid": False},
                },
            },
        )
        service = _service(StubFactorComboAPI([]), StubPerformanceAPI([]), StubSubFactorAPI(detail))

        result = service.verify_registered_sub_factor(801, "composite-test-factor")

        assert result["id"] == 801, result

    def test_refresh_network_error_is_reported_as_technical_failure(self) -> None:
        """刷新查询网络错误重试耗尽后分类为技术失败，并保留任务 ID 和异常类型。"""

        service = _service(
            StubFactorComboAPI([]),
            StubPerformanceAPI([requests.exceptions.SSLError("unexpected EOF") for _ in range(5)]),
            StubSubFactorAPI(StubResponse(200, {"success": True, "data": {}})),
        )

        with pytest.raises(FactorComboFlowError) as error:
            service.poll_performance_refresh("refresh-701", "composite-test-factor")

        assert error.value.outcome == FlowOutcome.FAIL_TECHNICAL, error.value
        assert "refresh-701" in str(error.value), str(error.value)
        assert "SSLError" in str(error.value), str(error.value)

    def test_refresh_poll_timeout_after_successful_status_is_refresh_failure(self) -> None:
        """刷新任务持续可读但长期处于 running 时，应分类为刷新超时而不是技术失败。"""

        service = _service(
            StubFactorComboAPI([]),
            StubPerformanceAPI([_active_refresh_response() for _ in range(5)]),
            StubSubFactorAPI(StubResponse(200, {"success": True, "data": {}})),
        )

        with pytest.raises(FactorComboFlowError) as error:
            service.poll_performance_refresh("refresh-701", "composite-test-factor")

        assert error.value.outcome == FlowOutcome.FAIL_REFRESH, error.value

    def test_refresh_recovers_from_network_error_then_times_out_as_refresh_failure(self) -> None:
        """刷新查询从网络错误恢复后若任务仍超时，以最后可读状态决定为刷新失败。"""

        responses: list[Any] = [requests.exceptions.SSLError("temporary EOF")]
        responses.extend(_active_refresh_response() for _ in range(4))
        service = _service(
            StubFactorComboAPI([]),
            StubPerformanceAPI(responses),
            StubSubFactorAPI(StubResponse(200, {"success": True, "data": {}})),
        )

        with pytest.raises(FactorComboFlowError) as error:
            service.poll_performance_refresh("refresh-701", "composite-test-factor")

        assert error.value.outcome == FlowOutcome.FAIL_REFRESH, error.value
        assert error.value.details["last"]["status"] == "running", error.value.details

    def test_sub_factor_query_retries_eventual_consistency_404(self) -> None:
        """刷新任务完成后子因子详情暂时 404 时有限重试，随后继续做指标证据校验。"""

        sub_factor_api = StubSubFactorAPI(
            [
                StubResponse(404, {"success": False, "error": "sub-factor not visible yet"}),
                StubResponse(
                    200,
                    {
                        "success": True,
                        "data": {
                            "id": 801,
                            "sub_factor_name": "composite-test-factor",
                            "factor_ic_summary_metrics": [{"ic": 0.11}],
                        },
                    },
                ),
            ]
        )
        service = _service(
            StubFactorComboAPI([]),
            StubPerformanceAPI([]),
            sub_factor_api,
        )

        result = service.verify_registered_sub_factor(801, "composite-test-factor", max_retries=1)

        assert result["id"] == 801, result
        assert sub_factor_api.calls == [(801, "timeseries"), (801, "timeseries")], sub_factor_api.calls

    def test_result_404_checks_run_status_before_retrying_result(self) -> None:
        """结构化结果暂时 404 时必须先查询 Run 状态，再继续读取同一 Run 的结果。"""

        api = StubRealFlowAPI(
            status_responses=[
                _run_status_response("combo-22-1111111111111111", "running", "wait"),
                _run_status_response("combo-22-1111111111111111", "completed", "read_result"),
            ],
            result_responses=[
                StubResponse(404, {"success": False, "error": "result not ready"}),
                StubResponse(404, {"success": False, "error": "result still not ready"}),
                _run_result_response("combo-22-1111111111111111", valid=False, continue_exploration=False),
            ],
        )
        service = _real_flow_service(api, max_rounds=1, max_technical_retries=2)
        run = RealRun(
            form=SubmittedForm(session_id=11, form_id=22, pool_id=33, status="processing"),
            pipeline_run_id="combo-22-1111111111111111",
        )

        result = service.read_real_pipeline_result(run, max_retries=2)

        assert result.review["experiment_valid"] is False, result
        assert api.status_calls == [
            (22, "combo-22-1111111111111111"),
            (22, "combo-22-1111111111111111"),
        ], api.status_calls
        assert api.result_calls == [
            (22, "combo-22-1111111111111111"),
            (22, "combo-22-1111111111111111"),
            (22, "combo-22-1111111111111111"),
        ], api.result_calls

    def test_result_404_followed_by_failed_run_is_technical_failure(self) -> None:
        """结构化结果 404 后若 Run 已失败，不能把缺少结果判成业务无效。"""

        api = StubRealFlowAPI(
            status_responses=[_run_status_response("combo-22-1111111111111111", "failed", "retry_run")],
            result_responses=[StubResponse(404, {"success": False, "error": "result not ready"})],
        )
        service = _real_flow_service(api, max_rounds=1, max_technical_retries=1)
        run = RealRun(
            form=SubmittedForm(session_id=11, form_id=22, pool_id=33, status="processing"),
            pipeline_run_id="combo-22-1111111111111111",
        )

        with pytest.raises(FactorComboFlowError) as error:
            service.read_real_pipeline_result(run, max_retries=1)

        assert error.value.outcome == FlowOutcome.FAIL_TECHNICAL, error.value
        assert "status_response" in error.value.details, error.value.details

    def test_exhausted_status_network_errors_do_not_start_a_new_run(self) -> None:
        """状态查询网络错误重试耗尽时保留原 Run，并明确禁止自动新建。"""

        api = StubRealFlowAPI(
            status_responses=[
                requests.exceptions.SSLError("unexpected EOF"),
                requests.exceptions.SSLError("unexpected EOF again"),
            ],
            result_responses=[],
        )
        service = _real_flow_service(api, max_rounds=1, max_technical_retries=1)
        form = SubmittedForm(session_id=11, form_id=22, pool_id=33, status="submitted")

        with pytest.raises(FactorComboFlowError) as error:
            service.run_real_research_flow(form, user_id=7)

        assert error.value.outcome == FlowOutcome.FAIL_TECHNICAL, error.value
        assert len(api.start_calls) == 1, api.start_calls
        assert error.value.details.get("retry_pipeline") is False, error.value.details

    def test_status_poll_timeout_does_not_start_a_new_run(self) -> None:
        """状态轮询超时时不把未知状态当作 Pipeline 失败，也不创建第二个 Run。"""

        api = StubRealFlowAPI(status_responses=[], result_responses=[])
        service = _real_flow_service(
            api,
            max_rounds=1,
            max_technical_retries=1,
            poll_timeout_seconds=0,
        )
        form = SubmittedForm(session_id=11, form_id=22, pool_id=33, status="submitted")

        with pytest.raises(FactorComboFlowError) as error:
            service.run_real_research_flow(form, user_id=7)

        assert error.value.outcome == FlowOutcome.FAIL_TECHNICAL, error.value
        assert len(api.start_calls) == 1, api.start_calls
        assert error.value.details.get("retry_pipeline") is False, error.value.details

    def test_result_network_errors_do_not_start_a_new_run_after_same_run_retries(self) -> None:
        """结果读取网络错误重试耗尽后不重新启动可能已完成的原 Run。"""

        api = StubRealFlowAPI(
            status_responses=[_run_status_response("combo-22-1111111111111111", "completed", "read_result")],
            result_responses=[
                requests.exceptions.SSLError("result connection lost"),
                requests.exceptions.SSLError("result connection still unavailable"),
            ],
        )
        service = _real_flow_service(api, max_rounds=1, max_technical_retries=1)
        form = SubmittedForm(session_id=11, form_id=22, pool_id=33, status="submitted")

        with pytest.raises(FactorComboFlowError) as error:
            service.run_real_research_flow(form, user_id=7)

        assert error.value.outcome == FlowOutcome.FAIL_TECHNICAL, error.value
        assert len(api.start_calls) == 1, api.start_calls
        assert error.value.details.get("retry_pipeline") is False, error.value.details

    def test_exhausted_result_404_does_not_start_duplicate_completed_run(self) -> None:
        """已完成 Run 的结构化结果持续 404 时保留原 Run，不盲目创建第二个 Run。"""

        api = StubRealFlowAPI(
            status_responses=[
                _run_status_response("combo-22-1111111111111111", "completed", "read_result"),
                _run_status_response("combo-22-1111111111111111", "completed", "read_result"),
                _run_status_response("combo-22-1111111111111111", "completed", "read_result"),
            ],
            result_responses=[
                StubResponse(404, {"success": False, "error": "result not ready"}),
                StubResponse(404, {"success": False, "error": "result still not ready"}),
            ],
        )
        service = _real_flow_service(api, max_rounds=1, max_technical_retries=1)
        form = SubmittedForm(session_id=11, form_id=22, pool_id=33, status="submitted")

        with pytest.raises(FactorComboFlowError) as error:
            service.run_real_research_flow(form, user_id=7)

        assert error.value.outcome == FlowOutcome.FAIL_TECHNICAL, error.value
        assert len(api.start_calls) == 1, api.start_calls
        assert error.value.details.get("retry_pipeline") is False, error.value.details


class TestRefreshEvidenceReconciliation:
    """验证刷新计算证据的身份、状态和 API/DB 严格对账规则。"""

    @staticmethod
    def _refresh_data() -> dict[str, Any]:
        """构造带明确 IC 计算 Run 的刷新完成响应。"""

        return {
            "results": [{"run_id": "ic-refresh-801"}],
        }

    @staticmethod
    def _validity_rows() -> list[dict[str, Any]]:
        """构造同时引用时序和截面 summary 的有效性快照。"""

        return [
            {
                "id": 904,
                "factor_id": 801,
                "is_sub_factor_id": 1,
                "run_id": "factor-validity-refresh-801",
                "time_series_summary_id": 1001,
                "time_series_summary_run_id": "ic-refresh-801",
                "time_series_summary_factor_id": 801,
                "time_series_summary_is_sub_factor_id": 1,
                "cross_sectional_summary_id": 1002,
                "cross_sectional_summary_run_id": "ic-refresh-801",
                "cross_sectional_summary_factor_id": 801,
                "cross_sectional_summary_is_sub_factor_id": 1,
            }
        ]

    @staticmethod
    def _metric_rows(run_status: str = "completed") -> list[dict[str, Any]]:
        """构造两条完整 summary 明细，分别代表时序和截面计算。"""

        return [
            {
                "summary_id": 1001,
                "factor_id": 801,
                "is_sub_factor_id": 1,
                "run_id": "ic-refresh-801",
                "run_status": run_status,
                "ic_scope": "time_series",
                "window_scope": "rolling",
                "mean_ic": Decimal("0.1"),
                "median_ic": Decimal("0.09"),
            },
            {
                "summary_id": 1002,
                "factor_id": 801,
                "is_sub_factor_id": 1,
                "run_id": "ic-refresh-801",
                "run_status": run_status,
                "ic_scope": "cross_sectional",
                "window_scope": "rolling",
                "mean_rank_ic": Decimal("0.2"),
            },
        ]

    def test_metric_summary_id_and_run_id_are_filtered_together(self) -> None:
        """指标同时带错 summary_id 对应的 Run 时必须失败，不能因 ID 命中而跳过 Run 校验。"""

        with pytest.raises(FactorComboFlowError) as error:
            FactorComboService._compare_api_and_database_refresh_data(
                801,
                {"factor_ic_summary_metrics": [{"summary_id": 1001, "run_id": "ic-old", "mean_ic": 0.1}]},
                [{"summary_id": 1001, "run_id": "ic-refresh-801", "mean_ic": 0.1}],
                [],
            )

        assert error.value.outcome == FlowOutcome.FAIL_CONTRACT, error.value

    def test_metric_scope_mismatch_is_not_silently_ignored(self) -> None:
        """指标明确声明错误的 IC 范围且无法匹配时必须报告契约失败。"""

        with pytest.raises(FactorComboFlowError) as error:
            FactorComboService._compare_api_and_database_refresh_data(
                801,
                {"factor_ic_summary_metrics": [{"summary_id": 1001, "ic_scope": "cross_sectional", "mean_ic": 0.1}]},
                [{"summary_id": 1001, "ic_scope": "time_series", "mean_ic": 0.1}],
                [],
            )

        assert error.value.outcome == FlowOutcome.FAIL_CONTRACT, error.value

    def test_explicit_metric_identity_matching_multiple_rows_is_contract_failure(self) -> None:
        """明确给出 summary ID 但数据库存在多条候选时不得静默跳过。"""

        with pytest.raises(FactorComboFlowError) as error:
            FactorComboService._compare_api_and_database_refresh_data(
                801,
                {"factor_ic_summary_metrics": [{"summary_id": 1001, "mean_ic": 0.1}]},
                [
                    {"summary_id": 1001, "run_id": "run-a", "mean_ic": 0.1},
                    {"summary_id": 1001, "run_id": "run-b", "mean_ic": 0.1},
                ],
                [],
            )

        assert error.value.outcome == FlowOutcome.FAIL_CONTRACT, error.value

    def test_api_metric_factor_identity_must_match_target_sub_factor(self) -> None:
        """API 指标返回其他因子 ID 时必须失败。"""

        with pytest.raises(FactorComboFlowError) as error:
            FactorComboService._compare_api_and_database_refresh_data(
                801,
                {
                    "factor_ic_summary_metrics": [
                        {"factor_id": 999, "is_sub_factor_id": True, "summary_id": 1001, "mean_ic": 0.1}
                    ]
                },
                [{"factor_id": 801, "is_sub_factor_id": 1, "summary_id": 1001, "mean_ic": 0.1}],
                [],
            )

        assert error.value.outcome == FlowOutcome.FAIL_CONTRACT, error.value

    def test_api_validity_identity_must_match_target_sub_factor(self) -> None:
        """API 有效性对象返回错误因子或母因子标识时必须失败。"""

        with pytest.raises(FactorComboFlowError) as error:
            FactorComboService._compare_api_and_database_refresh_data(
                801,
                {
                    "factor_validity_status": {
                        "id": 904,
                        "factor_id": 999,
                        "is_sub_factor_id": False,
                        "time_series_is_valid": True,
                    }
                },
                [],
                [{"id": 904, "factor_id": 801, "is_sub_factor_id": 1, "time_series_is_valid": True}],
            )

        assert error.value.outcome == FlowOutcome.FAIL_CONTRACT, error.value

    def test_explicit_null_metric_is_compared_with_database_null(self) -> None:
        """API 明确返回 null 且 DB 同字段为 null 时应记录为一次成功对账。"""

        matches = FactorComboService._compare_api_and_database_refresh_data(
            801,
            {"factor_ic_summary_metrics": [{"summary_id": 1001, "mean_ic": None}]},
            [{"summary_id": 1001, "mean_ic": None}],
            [],
        )

        assert matches[0]["fields"] == ("mean_ic",), matches

    def test_explicit_null_metric_with_missing_database_field_is_contract_failure(self) -> None:
        """API 明确返回 null 但 DB 缺少对应字段时不得跳过对账。"""

        with pytest.raises(FactorComboFlowError) as error:
            FactorComboService._compare_api_and_database_refresh_data(
                801,
                {"factor_ic_summary_metrics": [{"summary_id": 1001, "mean_ic": None}]},
                [{"summary_id": 1001}],
                [],
            )

        assert error.value.outcome == FlowOutcome.FAIL_CONTRACT, error.value

    def test_explicit_null_validity_is_compared_with_database_null(self) -> None:
        """API 明确返回 null 有效性且 DB 同字段为 null 时应通过该字段对账。"""

        matches = FactorComboService._compare_api_and_database_refresh_data(
            801,
            {"factor_validity_status": {"id": 904, "time_series_is_valid": None}},
            [],
            [{"id": 904, "time_series_is_valid": None}],
        )

        assert matches[0]["fields"] == ("time_series_is_valid",), matches

    def test_explicit_null_validity_with_missing_database_field_is_contract_failure(self) -> None:
        """API 明确返回 null 有效性但 DB 缺少字段时不得通过。"""

        with pytest.raises(FactorComboFlowError) as error:
            FactorComboService._compare_api_and_database_refresh_data(
                801,
                {"factor_validity_status": {"id": 904, "time_series_is_valid": None}},
                [],
                [{"id": 904}],
            )

        assert error.value.outcome == FlowOutcome.FAIL_CONTRACT, error.value

    def test_stratification_alias_is_compared_with_mean_stratification(self) -> None:
        """API 使用 stratification 别名时，必须与 DB 的 mean_stratification 字段对账。"""

        matches = FactorComboService._compare_api_and_database_refresh_data(
            801,
            {"factor_ic_summary_metrics": [{"summary_id": 1001, "stratification": Decimal("0.75")}]},
            [{"summary_id": 1001, "mean_stratification": Decimal("0.75")}],
            [],
        )

        assert matches[0]["fields"] == ("stratification",), matches

    def test_stratification_alias_mismatch_is_contract_failure(self) -> None:
        """API 的 stratification 与 DB 的 mean_stratification 不一致时必须失败。"""

        with pytest.raises(FactorComboFlowError) as error:
            FactorComboService._compare_api_and_database_refresh_data(
                801,
                {"factor_ic_summary_metrics": [{"summary_id": 1001, "stratification": 0.75}]},
                [{"summary_id": 1001, "mean_stratification": 0.5}],
                [],
            )

        assert error.value.outcome == FlowOutcome.FAIL_CONTRACT, error.value

    def test_equivalent_api_and_mysql_datetime_formats_match(self) -> None:
        """带时区的 API 时间与无时区的 MySQL DATETIME 墙上时间相同，必须定位到同一指标行。"""

        matches = FactorComboService._compare_api_and_database_refresh_data(
            801,
            {
                "factor_ic_summary_metrics": [
                    {
                        "summary_id": 1001,
                        "period_start": "2026-08-01T00:00:00+08:00",
                        "period_end": "2026-08-02T00:00:00+08:00",
                        "mean_ic": 0.1,
                    }
                ]
            },
            [
                {
                    "summary_id": 1001,
                    "period_start": datetime(2026, 8, 1, 0, 0, 0),
                    "period_end": datetime(2026, 8, 2, 0, 0, 0),
                    "mean_ic": 0.1,
                }
            ],
            [],
        )

        assert matches[0]["db_summary_id"] == 1001, matches

    def test_different_period_identity_is_contract_failure(self) -> None:
        """API 与 DB 的统计区间不同，即使 summary ID 相同也不能被当作同一条结果。"""

        with pytest.raises(FactorComboFlowError) as error:
            FactorComboService._compare_api_and_database_refresh_data(
                801,
                {
                    "factor_ic_summary_metrics": [
                        {
                            "summary_id": 1001,
                            "period_start": "2026-08-01T00:00:00+08:00",
                            "mean_ic": 0.1,
                        }
                    ]
                },
                [
                    {
                        "summary_id": 1001,
                        "period_start": "2026-08-02 00:00:00",
                        "mean_ic": 0.1,
                    }
                ],
                [],
            )

        assert error.value.outcome == FlowOutcome.FAIL_CONTRACT, error.value

    def test_same_instant_with_two_timezone_offsets_matches(self) -> None:
        """双方都带时区但表示同一 UTC 时刻时，时间身份应判定一致。"""

        assert FactorComboService._same_datetime_identity(
            "2026-08-01T00:00:00+08:00",
            "2026-07-31T16:00:00Z",
        ) is True

    def test_validity_period_is_part_of_database_row_identity(self) -> None:
        """有效性快照的统计区间不同于 API 时，即使快照 ID 相同也必须拒绝对账。"""

        with pytest.raises(FactorComboFlowError) as error:
            FactorComboService._compare_api_and_database_refresh_data(
                801,
                {
                    "factor_validity_status": {
                        "id": 904,
                        "period_start": "2026-08-01T00:00:00+08:00",
                        "period_end": "2026-08-02T00:00:00+08:00",
                        "time_series_is_valid": True,
                    }
                },
                [],
                [
                    {
                        "id": 904,
                        "period_start": "2026-08-01 00:00:00",
                        "period_end": "2026-08-03 00:00:00",
                        "time_series_is_valid": True,
                    }
                ],
            )

        assert error.value.outcome == FlowOutcome.FAIL_CONTRACT, error.value

    def test_explicit_null_period_is_compared_instead_of_treated_as_absent(self) -> None:
        """API 明确返回空统计区间时，DB 也必须明确保存空值。"""

        matches = FactorComboService._compare_api_and_database_refresh_data(
            801,
            {
                "factor_ic_summary_metrics": [
                    {"summary_id": 1001, "period_start": None, "period_end": None, "mean_ic": 0.1}
                ]
            },
            [{"summary_id": 1001, "period_start": None, "period_end": None, "mean_ic": 0.1}],
            [],
        )

        assert "period_start" in matches[0]["fields"], matches

        with pytest.raises(FactorComboFlowError) as error:
            FactorComboService._compare_api_and_database_refresh_data(
                801,
                {"factor_ic_summary_metrics": [{"summary_id": 1001, "period_start": None, "mean_ic": 0.1}]},
                [{"summary_id": 1001, "period_start": "2026-08-01 00:00:00", "mean_ic": 0.1}],
                [],
            )

        assert error.value.outcome == FlowOutcome.FAIL_CONTRACT, error.value

    @pytest.mark.parametrize(
        ("left", "right"),
        [(True, 1), (False, 0), ("true", 1), ("false", 0), (1, "1"), (0, "0")],
    )
    def test_boolean_and_mysql_tinyint_are_compared_by_business_value(self, left: Any, right: Any) -> None:
        """API JSON 布尔与 MySQL tinyint 或其字符串表示应按同一业务值比较。"""

        assert FactorComboService._same_scalar(left, right) is True

    def test_new_summary_metric_field_counts_as_calculation_evidence(self) -> None:
        """新版 summary 的中位数等扩展指标非空时也应被认定为真实计算结果。"""

        assert FactorComboService._is_populated_calculation_metric({"median_ic": 0}) is True
        assert FactorComboService._is_populated_calculation_metric({"positive_rank_ic_rate": 0}) is True
        assert FactorComboService._is_populated_calculation_metric({"oos_icir": 0}) is True
        assert FactorComboService._is_populated_calculation_metric({"mean_stratification": 0}) is True

    def test_plain_numeric_zero_keeps_decimal_tolerance(self) -> None:
        """普通数值零不能因为恰好是 0 而被强制走布尔比较。"""

        assert FactorComboService._same_scalar(0, Decimal("0.000000001")) is True

    @pytest.mark.parametrize(
        ("run_status", "expected_outcome"),
        [
            ("failed", FlowOutcome.FAIL_REFRESH),
            ("running", FlowOutcome.FAIL_REFRESH),
            ("unknown", FlowOutcome.FAIL_CONTRACT),
        ],
    )
    def test_calculation_run_status_is_classified_before_metric_acceptance(
        self,
        run_status: str,
        expected_outcome: str,
    ) -> None:
        """计算 Run 失败、进行中和未知状态必须分别按刷新失败或契约失败分类。"""

        with pytest.raises(FactorComboFlowError) as error:
            FactorComboService._validate_database_refresh_evidence(
                801,
                self._metric_rows(run_status),
                self._validity_rows(),
                self._refresh_data(),
            )

        assert error.value.outcome == expected_outcome, error.value

    def test_validity_summary_id_missing_from_detail_rows_is_contract_failure(self) -> None:
        """有效性快照引用的 summary ID 未出现在新版明细时必须失败。"""

        with pytest.raises(FactorComboFlowError) as error:
            FactorComboService._validate_database_refresh_evidence(
                801,
                [self._metric_rows()[0]],
                self._validity_rows(),
                self._refresh_data(),
            )

        assert error.value.outcome == FlowOutcome.FAIL_CONTRACT, error.value

    def test_validity_summary_run_id_mismatch_is_contract_failure(self) -> None:
        """有效性快照记录的 summary Run 与实际 summary 行不一致时必须失败。"""

        validity_rows = self._validity_rows()
        validity_rows[0]["cross_sectional_summary_run_id"] = "ic-other-run"

        with pytest.raises(FactorComboFlowError) as error:
            FactorComboService._validate_database_refresh_evidence(
                801,
                self._metric_rows(),
                validity_rows,
                self._refresh_data(),
            )

        assert error.value.outcome == FlowOutcome.FAIL_CONTRACT, error.value

    def test_complete_summary_and_validity_identity_chain_passes(self) -> None:
        """summary、有效性快照和刷新 Run 全部一致时应返回完整数据库证据。"""

        evidence = FactorComboService._validate_database_refresh_evidence(
            801,
            self._metric_rows(),
            self._validity_rows(),
            self._refresh_data(),
        )

        assert evidence.matched_run_ids == ("ic-refresh-801",), evidence
        assert {row["summary_id"] for row in evidence.calculation_metrics} == {1001, 1002}, evidence

    def test_every_refresh_run_must_be_linked_by_validity_snapshot(self) -> None:
        """刷新返回多个计算 Run 时，每个 Run 都必须被有效性快照的 summary 外键引用。"""

        refresh_data = {"results": [{"run_id": "ic-run-a"}, {"run_id": "ic-run-b"}]}
        validity_rows = self._validity_rows()
        validity_rows[0]["time_series_summary_run_id"] = "ic-run-a"
        validity_rows[0]["cross_sectional_summary_run_id"] = "ic-run-a"
        calculation_rows = [
            {
                "summary_id": 1001,
                "factor_id": 801,
                "is_sub_factor_id": 1,
                "run_id": "ic-run-a",
                "run_status": "completed",
                "mean_ic": 0.1,
            },
            {
                "summary_id": 1002,
                "factor_id": 801,
                "is_sub_factor_id": 1,
                "run_id": "ic-run-b",
                "run_status": "completed",
                "mean_ic": 0.2,
            },
        ]

        with pytest.raises(FactorComboFlowError) as error:
            FactorComboService._validate_database_refresh_evidence(
                801,
                calculation_rows,
                validity_rows,
                refresh_data,
            )

        assert error.value.outcome == FlowOutcome.FAIL_REFRESH, error.value
        assert error.value.details["missing_validity_run_ids"] == ["ic-run-b"], error.value

    @pytest.mark.parametrize(
        ("database_row", "expected_outcome"),
        [
            (None, FlowOutcome.FAIL_REFRESH),
            ({"id": 999, "sub_factor_name": "composite-test-factor", "type": 1}, FlowOutcome.FAIL_CONTRACT),
            ({"id": 801, "sub_factor_name": "different-factor", "type": 1}, FlowOutcome.FAIL_CONTRACT),
            ({"id": 801, "sub_factor_name": "composite-test-factor", "type": 2}, FlowOutcome.FAIL_CONTRACT),
        ],
    )
    def test_post_refresh_database_sub_factor_identity_is_validated(
        self,
        database_row: dict[str, Any] | None,
        expected_outcome: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """刷新后数据库子因子缺失或 ID、名称、类型错误时必须明确分类。"""

        repository = StubRepository({"id": 901, "combo_id": 701, "sub_factor_id": 801})
        monkeypatch.setattr(repository, "get_registered_sub_factor", lambda sub_factor_id: database_row)
        service = _service(
            StubFactorComboAPI([]),
            StubPerformanceAPI([]),
            StubSubFactorAPI(StubResponse(200, {"success": True, "data": {}})),
            repository=repository,
        )

        with pytest.raises(FactorComboFlowError) as error:
            service._read_database_sub_factor_after_refresh(801, "composite-test-factor")

        assert error.value.outcome == expected_outcome, error.value

    def test_post_refresh_database_sub_factor_query_error_is_technical_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """刷新后数据库子因子查询异常必须分类为技术失败。"""

        repository = StubRepository({"id": 901, "combo_id": 701, "sub_factor_id": 801})

        def raise_database_error(sub_factor_id: int) -> dict[str, Any] | None:
            """模拟数据库连接异常。"""

            raise RuntimeError("database connection lost")

        monkeypatch.setattr(repository, "get_registered_sub_factor", raise_database_error)
        service = _service(
            StubFactorComboAPI([]),
            StubPerformanceAPI([]),
            StubSubFactorAPI(StubResponse(200, {"success": True, "data": {}})),
            repository=repository,
        )

        with pytest.raises(FactorComboFlowError) as error:
            service._read_database_sub_factor_after_refresh(801, "composite-test-factor")

        assert error.value.outcome == FlowOutcome.FAIL_TECHNICAL, error.value

    def test_database_query_exception_is_technical_failure(self) -> None:
        """刷新证据查询发生数据库异常时必须分类为技术失败。"""

        class FailingRepository(StubRepository):
            """在计算证据读取阶段抛出数据库异常的替身。"""

            def get_factor_refresh_calculation_metrics(self, sub_factor_id: int) -> list[dict[str, Any]]:
                """模拟数据库连接错误。"""

                raise RuntimeError("database connection lost")

        service = FactorComboService(
            chat_api=None,  # type: ignore[arg-type]
            factor_combo_api=StubFactorComboAPI([]),  # type: ignore[arg-type]
            repository=FailingRepository({"id": 901, "combo_id": 701, "sub_factor_id": 801}),  # type: ignore[arg-type]
            settings=_settings(),
            scope=ResourceScope(),
            performance_api=StubPerformanceAPI([]),  # type: ignore[arg-type]
            sub_factor_api=StubSubFactorAPI(StubResponse(200, {"success": True, "data": {}})),  # type: ignore[arg-type]
        )

        with pytest.raises(FactorComboFlowError) as error:
            service.verify_database_refresh_evidence(
                801,
                903,
                self._refresh_data(),
            )

        assert error.value.outcome == FlowOutcome.FAIL_TECHNICAL, error.value


class TestRealResearchFlowBranches:
    """验证真实研究主流程的技术重试、反馈续轮和结果契约。"""

    def test_run_start_conflict_reuses_database_pipeline_run_and_protects_form(self) -> None:
        """启动接口 409 且表单已有合法 Run 时复用该 Run，不再发起第二次启动。"""

        api = StubStartConflictAPI(
            StubResponse(
                409,
                {
                    "success": False,
                    "error": "run already exists for this form",
                },
            )
        )
        repository = StubRepository(
            {"id": 901, "combo_id": 701, "version_id": 702, "sub_factor_id": 801},
            {
                "id": 22,
                "factor_combo_id": 702,
                "pipeline_run_id": "combo-22-abcdef0123456789",
                "status": "processing",
            },
        )
        scope = ResourceScope()
        service = FactorComboService(
            chat_api=None,  # type: ignore[arg-type]
            factor_combo_api=api,  # type: ignore[arg-type]
            repository=repository,  # type: ignore[arg-type]
            settings=_settings(),
            scope=scope,
        )
        form = SubmittedForm(session_id=11, form_id=22, pool_id=33, status="processing")

        run = service.start_real_run(form, agent_uid="agent-1")

        assert run.pipeline_run_id == "combo-22-abcdef0123456789", run
        assert run.reused_existing is True, run
        assert 22 in scope.protected_form_ids, scope.protected_form_ids
        assert len(api.calls) == 1, api.calls

    def test_missing_review_decision_is_a_contract_failure(self) -> None:
        """结果缺少评审决策字段时不得被误判为业务无效。"""

        run = RealRun(
            form=SubmittedForm(session_id=11, form_id=22, pool_id=33, status="processing"),
            pipeline_run_id="combo-22-abcdef0123456789",
        )
        response = _run_result_response(run.pipeline_run_id, valid=False, continue_exploration=False)
        body = response.json()
        del body["data"]["result"]["factor_combo_review"]["experiment_valid"]

        service = _service(
            StubFactorComboAPI([]),
            StubPerformanceAPI([]),
            StubSubFactorAPI(StubResponse(200, {"success": True, "data": {}})),
        )

        with pytest.raises(FactorComboFlowError) as error:
            service.require_real_pipeline_result(StubResponse(200, body), run)

        assert error.value.outcome == FlowOutcome.FAIL_CONTRACT, error.value

    def test_technical_pipeline_failure_uses_fresh_run_before_business_decision(self) -> None:
        """首轮 Pipeline 技术失败时使用 force_fresh_pipeline_run 重试，再处理真实结果。"""

        api = StubRealFlowAPI(
            status_responses=[
                _run_status_response("combo-22-1111111111111111", "failed", "retry_run"),
                _run_status_response("combo-22-2222222222222222", "completed", "read_result"),
            ],
            result_responses=[
                _run_result_response("combo-22-2222222222222222", valid=False, continue_exploration=False),
            ],
        )
        service = _real_flow_service(api, max_rounds=1, max_technical_retries=1)
        form = SubmittedForm(session_id=11, form_id=22, pool_id=33, status="submitted")

        flow = service.run_real_research_flow(form, user_id=7)

        assert flow.outcome == FlowOutcome.PASS_INVALID, flow
        assert [payload["force_fresh_pipeline_run"] for _, payload in api.start_calls] == [False, True], api.start_calls
        assert all(payload["agent_uid"] == "agent-1" for _, payload in api.start_calls), api.start_calls
        assert api.result_calls == [(22, "combo-22-2222222222222222")], api.result_calls

    def test_pipeline_status_network_error_retries_same_run_without_starting_another(self) -> None:
        """状态查询网络错误恢复时只重试同一个 Run，不应创建第二个 Run。"""

        api = StubRealFlowAPI(
            status_responses=[
                requests.exceptions.SSLError("unexpected EOF"),
                _run_status_response("combo-22-1111111111111111", "completed", "read_result"),
            ],
            result_responses=[
                _run_result_response("combo-22-1111111111111111", valid=False, continue_exploration=False),
            ],
        )
        service = _real_flow_service(api, max_rounds=1, max_technical_retries=1)
        form = SubmittedForm(session_id=11, form_id=22, pool_id=33, status="submitted")

        flow = service.run_real_research_flow(form, user_id=7)

        assert flow.outcome == FlowOutcome.PASS_INVALID, flow
        assert [payload["force_fresh_pipeline_run"] for _, payload in api.start_calls] == [False], api.start_calls
        assert api.status_calls == [
            (22, "combo-22-1111111111111111"),
            (22, "combo-22-1111111111111111"),
        ], api.status_calls

    def test_result_not_ready_is_retried_without_restarting_completed_pipeline(self) -> None:
        """Pipeline 已完成但结构化结果暂时 404 时只重试结果读取，不重新启动 Run。"""

        api = StubRealFlowAPI(
            status_responses=[
                _run_status_response("combo-22-1111111111111111", "completed", "read_result"),
                _run_status_response("combo-22-1111111111111111", "completed", "read_result"),
            ],
            result_responses=[
                StubResponse(404, {"success": False, "error": "result not ready"}),
                _run_result_response("combo-22-1111111111111111", valid=False, continue_exploration=False),
            ],
        )
        service = _real_flow_service(api, max_rounds=1, max_technical_retries=1)
        form = SubmittedForm(session_id=11, form_id=22, pool_id=33, status="submitted")

        flow = service.run_real_research_flow(form, user_id=7)

        assert flow.outcome == FlowOutcome.PASS_INVALID, flow
        assert len(api.start_calls) == 1, api.start_calls
        assert api.result_calls == [
            (22, "combo-22-1111111111111111"),
            (22, "combo-22-1111111111111111"),
        ], api.result_calls
        assert api.status_calls == [
            (22, "combo-22-1111111111111111"),
            (22, "combo-22-1111111111111111"),
        ], api.status_calls

    def test_invalid_result_submits_feedback_and_starts_next_research_round(self) -> None:
        """首轮业务无效且仍可探索时提交回复 2，并把反馈 ID 传入下一轮启动。"""

        api = StubRealFlowAPI(
            status_responses=[
                _run_status_response("combo-22-1111111111111111", "completed", "read_result"),
                _run_status_response("combo-22-2222222222222222", "completed", "read_result"),
            ],
            result_responses=[
                _run_result_response("combo-22-1111111111111111", valid=False, continue_exploration=True),
                _run_result_response("combo-22-2222222222222222", valid=False, continue_exploration=False),
            ],
            feedback_response=StubResponse(
                200,
                {
                    "success": True,
                    "data": {"feedback_id": 991, "feedback_round": 2, "feedback_status": "pending", "reply": 2},
                },
            ),
        )
        service = _real_flow_service(api, max_rounds=2, max_technical_retries=0)
        form = SubmittedForm(session_id=11, form_id=22, pool_id=33, status="submitted")

        flow = service.run_real_research_flow(form, user_id=7)

        assert flow.outcome == FlowOutcome.PASS_INVALID, flow
        assert len(flow.rounds) == 2, flow.rounds
        assert len(api.feedback_payloads) == 1, api.feedback_payloads
        assert api.feedback_payloads[0]["reply"] == 2, api.feedback_payloads
        assert api.start_calls[0][1].get("feedback_id") is None, api.start_calls
        assert api.start_calls[1][1].get("feedback_id") == 991, api.start_calls
