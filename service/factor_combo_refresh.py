"""组合因子登记后的刷新任务验收与 API/DB 对账。"""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import requests

from service.factor_combo_models import (
    DatabaseRefreshEvidence,
    FactorComboFlowError,
    FlowOutcome,
    PerformanceRefreshResult,
)


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

# 这些字段是刷新后能够证明 IC/ICIR 结果确实落到新版 summary 表的最小核心集合。
# 其他字段仍会逐字段对账，但允许因为样本不足或该评价维度不适用而为 NULL。
_CORE_SUMMARY_VALUE_FIELDS = (
    "mean_ic",
    "icir",
    "mean_rank_ic",
    "rank_icir",
)
_CROSS_SECTIONAL_BACKTEST_FIELDS = (
    "ic_t_stat",
    "rank_ic_t_stat",
    "monotonicity_ratio",
    "mean_long_short_return",
    "long_short_annual_return",
    "long_short_t_stat",
)
_SUMMARY_OOS_FIELDS = (
    "is_icir",
    "oos_icir",
    "icir_oos_retention",
    "rank_is_icir",
    "rank_oos_icir",
    "rank_icir_oos_retention",
)
_SUMMARY_SCORE_FIELDS = (
    "ic_score",
    "rank_ic_score",
    "icir_score",
    "rank_icir_score",
    "t_stat_score",
    "oos_retention_score",
    "monotonicity_score",
    "long_short_score",
    "final_score",
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

_DATETIME_IDENTITY_FIELDS = {
    "period_start",
    "period_end",
    "data_start",
    "data_end",
    "is_period_start",
    "is_period_end",
    "oos_period_start",
    "oos_period_end",
}

_SUMMARY_CONFIG_FIELDS: dict[str, tuple[str, ...]] = {
    "calculation_mode": ("calculation_mode",),
    "factor_bar_interval": ("factor_bar_interval", "factor_interval", "bar_interval"),
    "factor_window_bars": ("factor_window_bars", "window_bars"),
    "return_bar_interval": ("return_bar_interval", "return_interval"),
    "forward_return_bars": ("forward_return_bars", "forward_bars"),
    "window_scope": ("window_scope",),
    "metric_window_bars": ("metric_window_bars",),
    "metric_window_days": ("metric_window_days",),
    "period_start": ("period_start",),
    "period_end": ("period_end",),
}
_RUN_CONFIG_FIELDS: dict[str, tuple[str, ...]] = {
    "interval_value": ("interval_value",),
    "forward_return_horizon": ("forward_return_horizon",),
    "universe_key": ("universe_key",),
    "method": ("method",),
    "data_start": ("data_start",),
    "data_end": ("data_end",),
}


class FactorComboRefreshMixin:
    """提供 Performance Refresh 轮询、结果验收和 API/DB 对账能力。

    宿主 Service 负责注入 API、Repository、配置以及通用响应解析方法；本类只承载登记后的刷新业务职责，
    不直接创建刷新任务，也不包含 pytest 断言。
    """

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
                refresh_run_ids = self._extract_refresh_run_ids(refresh_data)
                slice_run_ids: list[str] = list(refresh_run_ids)
                if not slice_run_ids:
                    for validity_row in validity_snapshots:
                        if not isinstance(validity_row, dict):
                            continue
                        for prefix in ("time_series", "cross_sectional"):
                            linked_run_id = validity_row.get(f"{prefix}_summary_run_id")
                            if isinstance(linked_run_id, (str, int)) and not isinstance(linked_run_id, bool):
                                normalized_run_id = str(linked_run_id).strip()
                                if normalized_run_id and normalized_run_id not in slice_run_ids:
                                    slice_run_ids.append(normalized_run_id)
                slice_rows: Any | None = None
                slice_reader = getattr(self._repository, "get_factor_refresh_calculation_slices", None)
                if callable(slice_reader):
                    slice_rows = slice_reader(normalized_sub_factor_id, tuple(slice_run_ids))
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
                    slice_metrics=slice_rows,
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
        slice_metrics: Any | None = None,
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
        normalized_slice_metrics: list[dict[str, Any]] = []
        if slice_metrics is not None:
            if not isinstance(slice_metrics, list):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "factor_ic_slice_metrics repository result must be a list",
                    slice_metrics,
                )
            for row in slice_metrics:
                if not isinstance(row, dict):
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        "factor_ic_slice_metrics repository row must be an object",
                        slice_metrics,
                    )
                normalized_slice_metrics.append(dict(row))
        return DatabaseRefreshEvidence(
            sub_factor_id=sub_factor_id,
            calculation_runs=tuple(normalized_runs),
            validity_snapshots=tuple(normalized_validity),
            refresh_run_ids=tuple(refresh_run_ids),
            matched_run_ids=matched_run_ids,
            calculation_metrics=tuple(normalized_metrics),
            slice_metrics=tuple(normalized_slice_metrics),
            api_db_matches=tuple(api_db_matches),
            run_details=tuple(normalized_run_details),
        )

    def validate_core_metric_coverage(
        self,
        evidence: DatabaseRefreshEvidence,
    ) -> dict[str, Any]:
        """验收刷新后因子核心指标、回测指标和原始切片是否形成完整可追溯链路。

        参数 ``evidence`` 是 ``verify_database_refresh_evidence`` 返回的同一批次数据库证据。返回各评价范围、窗口、
        OOS 字段和切片数量的诊断字典；缺少时序/截面核心 IC、评分、截面回测指标或原始切片时抛出
        ``FactorComboFlowError(FAIL_REFRESH)``，因子、Run、范围或自然身份重复时抛出
        ``FactorComboFlowError(FAIL_CONTRACT)``。该方法不计算任何指标，也不把历史 Run 或登记占位快照当作本次结果。
        """

        if not isinstance(evidence, DatabaseRefreshEvidence):
            raise TypeError("evidence must be a DatabaseRefreshEvidence")
        metrics = [dict(row) for row in evidence.calculation_metrics]
        if not metrics:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_REFRESH,
                "refresh evidence has no factor_ic_summary_metrics detail rows",
                evidence,
            )

        expected_scopes: set[str] = set()
        for validity in evidence.validity_snapshots:
            for prefix, scope in (("time_series", "time_series"), ("cross_sectional", "cross_sectional")):
                if validity.get(f"{prefix}_summary_id") is not None:
                    expected_scopes.add(scope)
        if not expected_scopes:
            expected_scopes = {
                str(row.get("ic_scope", "")).strip().lower()
                for row in metrics
                if str(row.get("symbol", "")) == ""
            }
        expected_scopes.discard("")
        if not expected_scopes:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_REFRESH,
                "refresh evidence has no aggregate time-series or cross-sectional summary",
                {"factor_id": evidence.sub_factor_id, "metrics": metrics},
            )

        aggregate_rows = [row for row in metrics if row.get("symbol") in ("", None)]
        if not aggregate_rows:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_REFRESH,
                "factor_ic_summary_metrics has no aggregate rows for core metric validation",
                {"factor_id": evidence.sub_factor_id, "metrics": metrics},
            )

        seen_identities: set[tuple[Any, ...]] = set()
        scope_diagnostics: dict[str, dict[str, Any]] = {}
        for scope in sorted(expected_scopes):
            scope_rows = [
                row
                for row in aggregate_rows
                if str(row.get("ic_scope", "")).strip().lower() == scope
            ]
            if not scope_rows:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_REFRESH,
                    f"refresh evidence is missing aggregate {scope} summary metrics",
                    {"factor_id": evidence.sub_factor_id, "expected_scopes": sorted(expected_scopes)},
                )
            populated_scores = 0
            populated_backtest = 0
            oos_available = 0
            for row in scope_rows:
                row_factor_id = self._positive_int_or_failure(
                    row.get("factor_id"),
                    FlowOutcome.FAIL_CONTRACT,
                    "summary metric is missing factor_id during core metric validation",
                    row,
                )
                if row_factor_id != evidence.sub_factor_id or row.get("is_sub_factor_id") not in (True, 1):
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        "summary metric does not belong to the registered sub-factor",
                        {"expected_factor_id": evidence.sub_factor_id, "row": row},
                    )
                run_id = self._required_non_empty_string_or_failure(
                    row.get("run_id"),
                    FlowOutcome.FAIL_CONTRACT,
                    "summary metric is missing run_id during core metric validation",
                    row,
                )
                identity = (
                    run_id,
                    scope,
                    row.get("universe_key"),
                    row.get("symbol"),
                    row.get("window_scope"),
                    row.get("metric_window_bars"),
                    row.get("metric_window_days"),
                    row.get("period_start"),
                    row.get("period_end"),
                )
                if identity in seen_identities:
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        "duplicate natural identity found in aggregate factor_ic_summary_metrics",
                        {"identity": identity, "row": row},
                    )
                seen_identities.add(identity)
                missing_core_fields = [
                    field_name
                    for field_name in _CORE_SUMMARY_VALUE_FIELDS
                    if field_name not in row or row.get(field_name) is None
                ]
                if missing_core_fields:
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_REFRESH,
                        f"aggregate {scope} summary is missing core IC/ICIR values",
                        {"scope": scope, "missing_fields": missing_core_fields, "row": row},
                    )
                score_fields_present = [
                    field_name for field_name in _SUMMARY_SCORE_FIELDS if row.get(field_name) is not None
                ]
                if not score_fields_present:
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_REFRESH,
                        f"aggregate {scope} summary has no populated scoring field",
                        {"scope": scope, "row": row},
                    )
                populated_scores += 1
                if any(row.get(field_name) is not None for field_name in _CROSS_SECTIONAL_BACKTEST_FIELDS):
                    populated_backtest += 1
                if any(row.get(field_name) is not None for field_name in _SUMMARY_OOS_FIELDS):
                    oos_available += 1
                for field_name in (*_SUMMARY_OOS_FIELDS, *_CROSS_SECTIONAL_BACKTEST_FIELDS, *_SUMMARY_SCORE_FIELDS):
                    if field_name not in row:
                        raise FactorComboFlowError(
                            FlowOutcome.FAIL_CONTRACT,
                            f"summary metric is missing documented field {field_name}",
                            {"scope": scope, "row": row},
                        )
            if scope == "cross_sectional" and populated_backtest == 0:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_REFRESH,
                    "cross-sectional summary has no t-stat/stratification/long-short backtest evidence",
                    {"scope": scope, "rows": scope_rows},
                )
            scope_diagnostics[scope] = {
                "aggregate_row_count": len(scope_rows),
                "scoring_row_count": populated_scores,
                "backtest_row_count": populated_backtest,
                "oos_row_count": oos_available,
                "windows": sorted({str(row.get("window_scope")) for row in scope_rows}),
            }

        slice_rows = [dict(row) for row in evidence.slice_metrics]
        if not slice_rows:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_REFRESH,
                "refresh evidence has no factor_ic_slice_metrics detail rows",
                {"factor_id": evidence.sub_factor_id, "run_ids": evidence.matched_run_ids},
            )
        slice_diagnostics: dict[str, int] = {}
        seen_slice_identities: set[tuple[Any, ...]] = set()
        for scope in sorted(expected_scopes):
            scope_slices = [
                row for row in slice_rows if str(row.get("ic_scope", "")).strip().lower() == scope
            ]
            if not scope_slices:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_REFRESH,
                    f"refresh evidence is missing raw {scope} slice metrics",
                    {"factor_id": evidence.sub_factor_id, "slice_rows": slice_rows},
                )
            for row in scope_slices:
                row_factor_id = self._positive_int_or_failure(
                    row.get("factor_id"),
                    FlowOutcome.FAIL_CONTRACT,
                    "raw slice metric is missing factor_id",
                    row,
                )
                if row_factor_id != evidence.sub_factor_id or row.get("is_sub_factor_id") not in (True, 1):
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        "raw slice metric does not belong to the registered sub-factor",
                        row,
                    )
                run_id = self._required_non_empty_string_or_failure(
                    row.get("run_id"),
                    FlowOutcome.FAIL_CONTRACT,
                    "raw slice metric is missing run_id",
                    row,
                )
                if run_id not in evidence.matched_run_ids:
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        "raw slice metric belongs to a Run outside the refresh evidence",
                        {"row": row, "matched_run_ids": evidence.matched_run_ids},
                    )
                if row.get("ic") is None or row.get("rank_ic") is None:
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_REFRESH,
                        f"raw {scope} slice metric is missing IC or Rank IC",
                        row,
                    )
                identity = (
                    run_id,
                    scope,
                    row.get("universe_key"),
                    row.get("symbol"),
                    row.get("window_scope"),
                    row.get("sample_segment"),
                    row.get("slice_start"),
                    row.get("slice_end"),
                )
                if identity in seen_slice_identities:
                    raise FactorComboFlowError(
                        FlowOutcome.FAIL_CONTRACT,
                        "duplicate natural identity found in factor_ic_slice_metrics",
                        {"identity": identity, "row": row},
                    )
                seen_slice_identities.add(identity)
            slice_diagnostics[scope] = len(scope_slices)

        return {
            "factor_id": evidence.sub_factor_id,
            "validated": True,
            "run_ids": tuple(evidence.matched_run_ids),
            "scopes": scope_diagnostics,
            "slice_counts": slice_diagnostics,
        }

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
                    "calculation_mode",
                    "universe_key",
                    "factor_bar_interval",
                    "factor_window_bars",
                    "return_bar_interval",
                    "forward_return_bars",
                    "window_scope",
                    "interval_value",
                    "forward_return_horizon",
                    "symbol",
                    "metric_window_bars",
                    "metric_window_days",
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
            cls._validate_summary_metric_against_run(metric, run)

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
        if contexts_by_run:
            missing_context_run_ids = [run_id for run_id in expected_run_ids if run_id not in contexts_by_run]
            if missing_context_run_ids:
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    "Performance Refresh omitted Run context for one or more returned calculation Runs",
                    {
                        "expected_run_ids": expected_run_ids,
                        "context_run_ids": sorted(contexts_by_run),
                        "missing_context_run_ids": missing_context_run_ids,
                    },
                )
        return [rows_by_run[run_id] for run_id in expected_run_ids]

    @classmethod
    def _validate_summary_metric_against_run(
        cls,
        summary_metric: Mapping[str, Any],
        run_detail: Mapping[str, Any],
    ) -> None:
        """核对 summary 行与 ``factor_ic_runs`` 主表及其完整配置的维度一致性。

        参数 ``summary_metric`` 是同一计算 Run 的 ``factor_ic_summary_metrics`` 行，``run_detail`` 是对应的
        ``factor_ic_runs`` 主表记录。该方法不返回值；顶层周期、收益 horizon、样本池或明确写入
        ``config_json`` 的窗口字段不一致时抛出 ``FAIL_CONTRACT``。配置 JSON 只在明确提供某个字段时参与比较，避免
        对未知的嵌套配置结构进行猜测；但只要字段存在，就不会静默跳过。
        """

        for summary_field, run_field in (
            ("interval_value", "interval_value"),
            ("forward_return_horizon", "forward_return_horizon"),
            ("universe_key", "universe_key"),
        ):
            cls._compare_required_run_dimension(summary_metric, run_detail, summary_field, run_field)

        config_json = run_detail.get("config_json")
        if not isinstance(config_json, Mapping):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                "factor_ic_runs config_json must be an object while reconciling summary dimensions",
                {"summary": dict(summary_metric), "run": dict(run_detail)},
            )

        for run_field, aliases in _RUN_CONFIG_FIELDS.items():
            config_values = cls._configuration_values(config_json, aliases)
            if not config_values:
                continue
            run_value = run_detail.get(run_field)
            if run_value is None or not any(
                cls._same_identity_scalar(run_field, candidate, run_value) for candidate in config_values
            ):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"factor_ic_runs config_json differs from the Run column at {run_field}",
                    {
                        "field": run_field,
                        "config_values": config_values,
                        "run_value": run_value,
                        "run": dict(run_detail),
                    },
                )

        for summary_field, aliases in _SUMMARY_CONFIG_FIELDS.items():
            if summary_field not in summary_metric:
                continue
            config_values = cls._configuration_values(config_json, aliases)
            if not config_values:
                continue
            summary_value = summary_metric.get(summary_field)
            if not any(
                cls._same_identity_scalar(summary_field, summary_value, candidate)
                for candidate in config_values
            ):
                raise FactorComboFlowError(
                    FlowOutcome.FAIL_CONTRACT,
                    f"factor_ic_summary_metrics differs from factor_ic_runs config_json at {summary_field}",
                    {
                        "field": summary_field,
                        "summary_value": summary_value,
                        "config_values": config_values,
                        "summary": dict(summary_metric),
                        "run": dict(run_detail),
                    },
                )

    @classmethod
    def _compare_required_run_dimension(
        cls,
        summary_metric: Mapping[str, Any],
        run_detail: Mapping[str, Any],
        summary_field: str,
        run_field: str,
    ) -> None:
        """比较 summary 与 Run 的必需顶层维度，并统一报告缺失或不一致。"""

        if summary_field not in summary_metric or summary_metric.get(summary_field) is None:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"summary/Run reconciliation is missing {summary_field} in summary",
                {"field": summary_field, "summary": dict(summary_metric), "run": dict(run_detail)},
            )
        if run_field not in run_detail or run_detail.get(run_field) is None:
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"summary/Run reconciliation is missing {run_field} in factor_ic_runs",
                {"field": run_field, "summary": dict(summary_metric), "run": dict(run_detail)},
            )
        if not cls._same_identity_scalar(
            summary_field,
            summary_metric.get(summary_field),
            run_detail.get(run_field),
        ):
            raise FactorComboFlowError(
                FlowOutcome.FAIL_CONTRACT,
                f"summary metric and factor_ic_runs differ at {summary_field}",
                {
                    "field": summary_field,
                    "summary": summary_metric.get(summary_field),
                    "run": run_detail.get(run_field),
                    "summary_row": dict(summary_metric),
                    "run_detail": dict(run_detail),
                },
            )

    @classmethod
    def _configuration_values(
        cls,
        config: Mapping[str, Any],
        aliases: tuple[str, ...],
    ) -> list[Any]:
        """从 Run 配置 JSON 中读取某个维度的明确值。

        参数 ``config`` 是已解析的 ``factor_ic_runs.config_json``，``aliases`` 是允许的字段名别名。返回去重前的
        候选值列表；优先读取配置根节点中的精确字段，根节点没有时再读取嵌套对象。该方法不推断数组位置或字段
        含义，调用方负责处理多个合法候选值。
        """

        normalized_aliases = {alias.casefold() for alias in aliases}
        direct_values: list[Any] = []
        nested_values: list[Any] = []

        def append_values(target: list[Any], value: Any) -> None:
            """把配置字段值展开为可逐项比较的候选值。"""

            if isinstance(value, (list, tuple)):
                target.extend(value)
            else:
                target.append(value)

        def visit(node: Any, *, root: bool) -> None:
            """递归访问 JSON 对象并收集精确字段名。"""

            if isinstance(node, Mapping):
                for key, value in node.items():
                    if str(key).strip().casefold() in normalized_aliases:
                        append_values(direct_values if root else nested_values, value)
                    if isinstance(value, (Mapping, list, tuple)):
                        visit(value, root=False)
            elif isinstance(node, (list, tuple)):
                for item in node:
                    if isinstance(item, (Mapping, list, tuple)):
                        visit(item, root=False)

        visit(config, root=True)
        return direct_values or nested_values

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

        return FactorComboRefreshMixin._has_identity_value(data, fields)

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

        left_bool = FactorComboRefreshMixin._coerce_boolean(left)
        right_bool = FactorComboRefreshMixin._coerce_boolean(right)
        if FactorComboRefreshMixin._has_explicit_boolean_semantics(left) or FactorComboRefreshMixin._has_explicit_boolean_semantics(
            right
        ):
            return left_bool is not None and right_bool is not None and left_bool == right_bool

        left_decimal = FactorComboRefreshMixin._coerce_decimal(left)
        right_decimal = FactorComboRefreshMixin._coerce_decimal(right)
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

        left_datetime = FactorComboRefreshMixin._parse_datetime_identity(left)
        right_datetime = FactorComboRefreshMixin._parse_datetime_identity(right)
        if left_datetime is None or right_datetime is None:
            return FactorComboRefreshMixin._same_scalar(left, right)

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
