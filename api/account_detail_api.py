"""账户详情接口的协议层封装。

已确认的 summary 端点使用固定路径；其余条件接口通过环境变量绑定，避免在
接口字段尚未确认时把猜测写进测试代码。
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Mapping
from urllib.parse import quote

import requests

from api.client import HTTPClient


class AccountDetailEndpointNotConfigured(ValueError):
    """表示当前账户详情接口的条件端点尚未完成契约绑定。"""


@dataclass(frozen=True)
class AccountDetailEndpointConfig:
    """保存账户详情 N01-N23 端点的相对路径配置。"""

    summary: str = "/quantitative-trading/analytics/summary"
    series: str = ""
    risk: str = ""
    assignments: str = ""
    positions: str = ""
    signals: str = ""
    batches: str = ""
    plans: str = ""
    intents: str = ""
    orders: str = ""
    fills: str = ""
    cashflows: str = ""
    bills: str = ""
    bill_detail: str = ""
    receipt: str = ""
    options: str = ""
    mapping: str = ""

    @classmethod
    def from_environment(cls) -> "AccountDetailEndpointConfig":
        """从环境变量读取已确认的条件端点路径。"""

        def value(name: str, default: str = "") -> str:
            return os.getenv(name, default).strip()

        return cls(
            summary=value("AUTOMATION_ACCOUNT_DETAIL_N01_PATH", cls.summary),
            series=value("AUTOMATION_ACCOUNT_DETAIL_N02_PATH"),
            risk=value("AUTOMATION_ACCOUNT_DETAIL_N04_PATH"),
            assignments=value("AUTOMATION_ACCOUNT_DETAIL_N09_PATH"),
            positions=value("AUTOMATION_ACCOUNT_DETAIL_N13_PATH"),
            signals=value("AUTOMATION_ACCOUNT_DETAIL_N14_PATH"),
            batches=value("AUTOMATION_ACCOUNT_DETAIL_N15_PATH"),
            plans=value("AUTOMATION_ACCOUNT_DETAIL_N16_PATH"),
            intents=value("AUTOMATION_ACCOUNT_DETAIL_N17_PATH"),
            orders=value("AUTOMATION_ACCOUNT_DETAIL_N18_PATH"),
            fills=value("AUTOMATION_ACCOUNT_DETAIL_N19_PATH"),
            cashflows=value("AUTOMATION_ACCOUNT_DETAIL_N20_PATH"),
            bills=value("AUTOMATION_ACCOUNT_DETAIL_N21_PATH"),
            bill_detail=value("AUTOMATION_ACCOUNT_DETAIL_N22_PATH"),
            receipt=value("AUTOMATION_ACCOUNT_DETAIL_N23_PATH"),
            options=value("AUTOMATION_ACCOUNT_DETAIL_N30_PATH"),
            mapping=value("AUTOMATION_ACCOUNT_DETAIL_N32_PATH"),
        )


class AccountDetailAPI:
    """封装账户详情的读取和到账登记 HTTP 协议。"""

    def __init__(
        self,
        client: HTTPClient,
        endpoints: AccountDetailEndpointConfig | None = None,
    ) -> None:
        """保存 HTTP 客户端和端点路径，不执行网络请求。"""

        self._client = client
        self._endpoints = endpoints or AccountDetailEndpointConfig.from_environment()

    @property
    def endpoints(self) -> AccountDetailEndpointConfig:
        """返回当前端点配置，供 Service 判断契约是否已绑定。"""

        return self._endpoints

    def summary(
        self,
        account_id: int | str,
        *,
        snapshot_id: str | None = None,
        scope: str = "lifetime",
        metric_basis: str = "accrual",
        metrics: str | None = None,
        from_at: str | None = None,
        to_at: str | None = None,
        metric_source: str | None = None,
    ) -> requests.Response:
        """读取账户摘要 N01。"""

        params: dict[str, Any] = {
            "subject_type": "account",
            "subject_id": str(account_id),
            "scope": scope,
            "metric_basis": metric_basis,
        }
        if snapshot_id is not None:
            params["snapshot_id"] = snapshot_id
        if metrics is not None:
            params["metrics"] = metrics
        if from_at is not None:
            params["from"] = from_at
        if to_at is not None:
            params["to"] = to_at
        if metric_source is not None:
            params["metric_source"] = metric_source
        return self._get(self._endpoints.summary, params)

    def series(self, account_id: int | str, **params: Any) -> requests.Response:
        """读取净值、收益或趋势曲线 N02；路径未绑定时抛出契约异常。"""

        return self._get(self._path("series"), self._account_params(account_id, params))

    def risk(self, account_id: int | str, **params: Any) -> requests.Response:
        """读取风险指标 N04。"""

        return self._get(self._path("risk"), self._account_params(account_id, params))

    def account_resource(self, resource: str, account_id: int | str, **params: Any) -> requests.Response:
        """读取一个按账户维度绑定的 N09/N13-N20 资源。"""

        return self._get(self._path(resource), self._account_params(account_id, params))

    def bills(self, account_id: int | str, **params: Any) -> requests.Response:
        """读取历史分润账单 N21。"""

        return self._get(self._path("bills"), self._account_params(account_id, params))

    def bill_detail(self, bill_id: int | str, **params: Any) -> requests.Response:
        """读取单笔账单详情 N22。"""

        path = self._path("bill_detail").format(bill_id=quote(str(bill_id), safe=""))
        return self._get(path, params)

    def register_receipt(
        self,
        bill_id: int | str,
        payload: Mapping[str, Any],
        *,
        idempotency_key: str,
    ) -> requests.Response:
        """提交到账登记 N23；写请求禁止自动重试。"""

        if not idempotency_key.strip():
            raise ValueError("idempotency_key must not be blank")
        path = self._path("receipt").format(bill_id=quote(str(bill_id), safe=""))
        headers = {
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_key,
        }
        return self._client.request(
            "POST",
            path,
            json_body=dict(payload),
            headers=headers,
            retryable=False,
            allow_redirects=False,
        )

    def _path(self, name: str) -> str:
        """读取一个已配置的相对路径；未绑定时明确阻断。"""

        path = getattr(self._endpoints, name)
        if not path:
            raise AccountDetailEndpointNotConfigured(
                f"账户详情端点 {name} 尚未绑定；请配置对应 AUTOMATION_ACCOUNT_DETAIL_*_PATH"
            )
        if not path.startswith("/") or "?" in path or ".." in path:
            raise ValueError(f"账户详情端点必须是无查询参数的相对路径: {name}")
        return path

    @staticmethod
    def _account_params(account_id: int | str, params: Mapping[str, Any]) -> dict[str, Any]:
        """合并账户身份参数，不修改调用方字典。"""

        result = dict(params)
        result.setdefault("account_id", str(account_id))
        return result

    def _get(self, path: str, params: Mapping[str, Any]) -> requests.Response:
        """发送不可变 GET，不自动替换账户身份或结果。"""

        return self._client.request(
            "GET",
            path,
            params=dict(params),
            headers={"X-Quant-Contract": "2", "Cache-Control": "no-cache"},
            # All account-detail reads are idempotent and may safely use the
            # client's configured transient-error retry policy.
            retryable=None,
            allow_redirects=False,
        )
