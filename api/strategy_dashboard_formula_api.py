"""策略看板公式验证所需的测试注入/计算端点协议封装。

本模块仅在已核实并配置对应测试端点时使用，不要求后端新建这些端点。
已接入的数据库来源路径不依赖本模块。生产环境不会自动启用，也不会把公开的
历史回补接口误当成任意原始数据注入接口；所有路径保持显式配置。
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
from typing import Any
from urllib.parse import quote, urlsplit

import requests

from api.client import HTTPClient


class FormulaInjectionContractError(ValueError):
    """测试环境注入/计算端点配置不完整。"""


@dataclass(frozen=True)
class FormulaFlowEndpointConfig:
    """保存公式闭环所需的五类测试端点路径。"""

    fixture_path: str
    trigger_path: str
    status_path_template: str
    result_path_template: str
    cleanup_path_template: str

    @classmethod
    def from_environment(cls) -> "FormulaFlowEndpointConfig":
        """从环境变量读取测试端点；未配置时返回空配置供上层明确阻断。"""

        return cls(
            fixture_path=os.getenv("QUEST_FORMULA_FIXTURE_PATH", "").strip(),
            trigger_path=os.getenv("QUEST_FORMULA_TRIGGER_PATH", "").strip(),
            status_path_template=os.getenv("QUEST_FORMULA_STATUS_PATH", "").strip(),
            result_path_template=os.getenv("QUEST_FORMULA_RESULT_PATH", "").strip(),
            cleanup_path_template=os.getenv("QUEST_FORMULA_CLEANUP_PATH", "").strip(),
        )

    def missing_fields(self) -> tuple[str, ...]:
        """返回尚未配置的端点名称。"""

        values = {
            "QUEST_FORMULA_FIXTURE_PATH": self.fixture_path,
            "QUEST_FORMULA_TRIGGER_PATH": self.trigger_path,
            "QUEST_FORMULA_STATUS_PATH": self.status_path_template,
            "QUEST_FORMULA_RESULT_PATH": self.result_path_template,
            "QUEST_FORMULA_CLEANUP_PATH": self.cleanup_path_template,
        }
        return tuple(name for name, value in values.items() if not value)

    def validate(self) -> None:
        """要求五个端点全部存在；不允许半配置状态悄悄跳过闭环。"""

        missing = self.missing_fields()
        if missing:
            raise FormulaInjectionContractError(
                "公式原始数据闭环端点未完整配置: " + ", ".join(missing)
            )
        for path, parameter in (
            (self.fixture_path, None), (self.trigger_path, None),
            (self.status_path_template, "run_id"), (self.result_path_template, "run_id"),
            (self.cleanup_path_template, "fixture_id"),
        ):
            parts = urlsplit(path)
            if not path.startswith("/") or path.startswith("//") or parts.scheme or parts.netloc or parts.query or parts.fragment or ".." in path or "\\" in path:
                raise FormulaInjectionContractError("公式端点必须为无查询参数的同主机相对路径")
            fields = re.findall(r"\{([^{}]+)\}", path)
            if fields != ([parameter] if parameter else []):
                raise FormulaInjectionContractError(f"公式端点路径参数应为 {parameter or '无'}")


class StrategyDashboardFormulaAPI:
    """封装测试环境的原始数据注入、计算触发、状态、结果和清理请求。"""

    def __init__(
        self,
        client: HTTPClient,
        endpoints: FormulaFlowEndpointConfig | None = None,
        *,
        environment: str,
        base_url: str,
        allowed_hosts: tuple[str, ...],
    ) -> None:
        """保存 HTTP 客户端和端点配置，不发送请求。"""

        self._client = client
        self._endpoints = endpoints or FormulaFlowEndpointConfig.from_environment()
        parsed = urlsplit(base_url)
        if environment != "test" or parsed.hostname not in allowed_hosts or parsed.username or parsed.password:
            raise FormulaInjectionContractError("公式写入仅允许已配置的 test 环境主机")
        if parsed.scheme != "https":
            raise FormulaInjectionContractError("测试后端必须使用 HTTPS")
        if client.base_url.rstrip("/") != base_url.rstrip("/"):
            raise FormulaInjectionContractError("声明测试地址与 HTTPClient 实际地址不一致")

    def close(self) -> None:
        """释放 HTTP 连接；无业务请求。"""
        self._client.close()

    @property
    def endpoints(self) -> FormulaFlowEndpointConfig:
        """返回当前端点配置。"""

        return self._endpoints

    def write_fixture(self, payload: dict[str, Any]) -> requests.Response:
        """写入一批测试上游原始数据；POST 不自动重试以避免重复写入。"""

        self._endpoints.validate()
        return self._client.request(
            "POST",
            self._endpoints.fixture_path,
            json_body=payload,
            headers={"Content-Type": "application/json"},
            retryable=False,
            allow_redirects=False,
        )

    def trigger_calculation(self, payload: dict[str, Any]) -> requests.Response:
        """触发该批原始数据的接入/公式计算任务。"""

        self._endpoints.validate()
        return self._client.request(
            "POST",
            self._endpoints.trigger_path,
            json_body=payload,
            headers={"Content-Type": "application/json"},
            retryable=False,
            allow_redirects=False,
        )

    def get_status(self, run_id: str) -> requests.Response:
        """查询计算任务状态。"""

        path = self._endpoints.status_path_template.format(run_id=quote(run_id, safe=""))
        return self._client.request("GET", path, retryable=True, allow_redirects=False)

    def get_result(self, run_id: str, query: dict[str, Any] | None = None) -> requests.Response:
        """查询已完成任务的最终公式结果。"""

        path = self._endpoints.result_path_template.format(run_id=quote(run_id, safe=""))
        return self._client.request("GET", path, params=query or {}, headers={"Cache-Control": "no-cache"}, retryable=True, allow_redirects=False)

    def cleanup(self, fixture_id: str) -> requests.Response:
        """删除本次运行创建的隔离测试数据；清理请求不自动重试。"""

        path = self._endpoints.cleanup_path_template.format(fixture_id=quote(fixture_id, safe=""))
        return self._client.request("DELETE", path, retryable=False, allow_redirects=False)
