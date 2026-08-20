"""组合因子台十个接口的协议封装。"""

from __future__ import annotations

from typing import Any

import requests

from api.client import HTTPClient


class FactorComboAPI:
    """按接口文档封装组合因子台端点，不包含业务断言。"""

    def __init__(self, client: HTTPClient) -> None:
        """初始化组合因子 API。

        参数 ``client`` 是已经配置基础地址、鉴权、超时和重试的 ``HTTPClient``。
        不返回值；十个组合因子接口均通过该客户端发送请求。
        """

        self._client = client

    def submit_form(self, payload: dict[str, Any]) -> requests.Response:
        """提交组合因子研究表单。

        参数 ``payload`` 是文档规定的表单 JSON 请求体。
        返回原始 HTTP 响应；表单、因子池和幂等结果由调用方断言。
        """

        return self._client.request(
            "POST",
            "/factor-combo/forms/submit",
            json_body=payload,
            headers={"Content-Type": "application/json"},
            retryable=True,
        )

    def get_work_order(self, form_id: int) -> requests.Response:
        """读取组合表单的工作单。

        参数 ``form_id`` 是提交接口返回的表单 ID。
        返回原始 HTTP 响应；工作单和锁定因子池由调用方断言。
        """

        return self._client.request("GET", f"/factor-combo/forms/{form_id}/work-order")

    def start_run(self, form_id: int, payload: dict[str, Any]) -> requests.Response:
        """启动表单对应的真实组合任务。

        参数 ``form_id`` 是已提交表单 ID，``payload`` 包含 agent_uid、可选 feedback_id 和强制刷新标记。
        返回原始 HTTP 响应；运行 ID 和幂等状态由调用方断言。请求要求强制新建 Run 时不启用底层自动重试，
        避免第一次请求已经被服务端接受但响应丢失后重复创建 Pipeline。
        """

        force_fresh_value = payload.get("force_fresh_pipeline_run")
        force_fresh = force_fresh_value is True or (
            isinstance(force_fresh_value, str) and force_fresh_value.strip().lower() == "true"
        )
        return self._client.request(
            "POST",
            f"/factor-combo/forms/{form_id}/runs",
            json_body=payload,
            headers={"Content-Type": "application/json"},
            retryable=not force_fresh,
        )

    def get_run_status(self, form_id: int, run_id: str) -> requests.Response:
        """查询组合任务运行状态。

        参数 ``form_id`` 是表单 ID，``run_id`` 是启动接口返回的 Pipeline 运行 ID。
        返回原始 HTTP 响应；状态、下一步动作和失败原因由调用方断言。
        """

        return self._client.request("GET", f"/factor-combo/forms/{form_id}/runs/{run_id}")

    def get_run_result(self, form_id: int, run_id: str) -> requests.Response:
        """读取已完成组合任务的结构化结果。

        参数 ``form_id`` 是表单 ID，``run_id`` 是已完成运行 ID。
        返回原始 HTTP 响应；报告、评审和有效性快照由调用方断言。
        """

        return self._client.request("GET", f"/factor-combo/forms/{form_id}/runs/{run_id}/result")

    def claim_legacy_pipeline(self, form_id: int, payload: dict[str, Any]) -> requests.Response:
        """认领测试环境中的兼容 Pipeline 任务。

        参数 ``form_id`` 是已提交表单 ID，``payload`` 包含会话 ID、兼容运行 ID、可选反馈 ID 和模拟模式标记。
        返回原始 HTTP 响应；该辅助端点仅用于准备 Worker 回调接口的合法前置状态。
        """

        return self._client.request(
            "POST",
            f"/factor-combo/forms/{form_id}/legacy-pipeline/claim",
            json_body=payload,
            headers={"Content-Type": "application/json"},
            retryable=True,
        )

    def submit_feedback(self, payload: dict[str, Any]) -> requests.Response:
        """提交组合报告的用户反馈。

        参数 ``payload`` 包含 session_id、form_id、pipeline_run_id、reply 和 feedback。
        返回原始 HTTP 响应；反馈记录和状态流转由调用方断言。
        """

        return self._client.request(
            "POST",
            "/factor-combo/reports/feedback",
            json_body=payload,
            headers={"Content-Type": "application/json"},
            retryable=True,
        )

    def register_report(self, payload: dict[str, Any]) -> requests.Response:
        """登记满足有效性条件的组合因子报告。

        参数 ``payload`` 包含会话、表单、运行、报告和有效性快照数据。
        返回原始 HTTP 响应；复合子因子和登记记录由调用方断言。
        """

        return self._client.request(
            "POST",
            "/factor-combo/reports/register",
            json_body=payload,
            headers={"Content-Type": "application/json"},
            retryable=True,
        )

    def create_initial_version(self, form_id: int, payload: dict[str, Any]) -> requests.Response:
        """创建 Worker 回写的初始组合版本。

        参数 ``form_id`` 是已被 Worker 认领的表单 ID，``payload`` 包含运行 ID、组合 ID、生成方式和组件。
        返回原始 HTTP 响应；版本、组件和表单指针由调用方断言。
        """

        return self._client.request(
            "POST",
            f"/factor-combo/forms/{form_id}/versions",
            json_body=payload,
            headers={"Content-Type": "application/json"},
            retryable=True,
        )

    def write_experiment(self, experiment_id: str, payload: dict[str, Any]) -> requests.Response:
        """写入 Worker 回写的组合因子实验结果。

        参数 ``experiment_id`` 是实验路径参数，``payload`` 包含表单、运行、配置、指标、Artifact 和有效性字段。
        返回原始 HTTP 响应；实验、表单和组合关联由调用方断言。
        """

        return self._client.request(
            "PUT",
            f"/factor-combo/experiments/{experiment_id}",
            json_body=payload,
            headers={"Content-Type": "application/json"},
            retryable=True,
        )

    def create_next_version(self, feedback_id: int, payload: dict[str, Any]) -> requests.Response:
        """创建 Feedback 对应的下一轮组合版本。

        参数 ``feedback_id`` 是已被 Worker 认领的反馈 ID，``payload`` 包含新的运行 ID、生成方式和组件。
        返回原始 HTTP 响应；下一版本及 Feedback、表单指针由调用方断言。
        """

        return self._client.request(
            "POST",
            f"/factor-combo/feedbacks/{feedback_id}/next-version",
            json_body=payload,
            headers={"Content-Type": "application/json"},
            retryable=True,
        )
