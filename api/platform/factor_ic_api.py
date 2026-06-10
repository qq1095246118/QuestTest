"""因子库 FactorIC 模块原始 API 调用封装。

本模块只负责拼接请求参数并发起 HTTP 调用，不做业务判断或断言。
"""

from __future__ import annotations

from typing import Any

from config.settings import settings
from service.common.http.http_client import HTTPClient


class FactorICAPI:
    """FactorIC 接口原始请求封装。

    请求参数:
        实例化时可传入 token，用于访问需要鉴权的接口。
    返回值:
        提供 FactorIC HTTP 请求方法的 API 客户端实例。
    """

    def __init__(self, token: str | None = None):
        """初始化 FactorIC API 客户端。

        请求参数:
            token: 可选 JWT token；传入后自动写入 Authorization header。
        返回值:
            无，实例化后保存 base_url 和默认请求头。
        """
        self.base_url = settings.base_url.rstrip("/")
        self.headers = {"Content-Type": "application/json"}
        if token is not None:
            self.headers["Authorization"] = f"Bearer {token}"

    def get(self, endpoint: str, params: dict[str, Any] | None = None):
        """发送 GET 请求。

        请求参数:
            endpoint: 以 / 开头的接口路径。
            params: 查询参数字典，值为 None 的字段会被过滤。
        返回值:
            requests.Response 对象；HTTPClient 会对 4xx/5xx 抛出 HTTPError。
        """
        url = f"{self.base_url}{endpoint}"
        return HTTPClient.request("GET", url, headers=self.headers, params=self.clean_params(params))

    def post(self, endpoint: str, json: dict[str, Any] | None = None):
        """发送 POST 请求。

        请求参数:
            endpoint: 以 / 开头的接口路径。
            json: 请求 JSON body。
        返回值:
            requests.Response 对象；HTTPClient 会对 4xx/5xx 抛出 HTTPError。
        """
        url = f"{self.base_url}{endpoint}"
        return HTTPClient.request("POST", url, headers=self.headers, json=json)

    def get_factor_by_symbol(self, factor_id: Any, **params: Any):
        """调用因子 IC by-symbol 旧汇总接口。

        请求参数:
            factor_id: 因子 ID。
            **params: ic_scope、time_window 等查询参数。
        返回值:
            因子 IC by-symbol 接口 requests.Response 对象。
        """
        return self.get(f"/api/v1/factor-ic/factors/{factor_id}/by-symbol", params)

    def get_factor_slice_metrics(self, factor_id: Any, **params: Any):
        """调用因子 IC 切片指标接口。

        请求参数:
            factor_id: 因子 ID。
            **params: ic_scope、symbol 等查询参数。
        返回值:
            因子 IC 切片指标接口 requests.Response 对象。
        """
        return self.get(f"/api/v1/factor-ic/factors/{factor_id}/slice-metrics", params)

    def get_factor_summary(self, factor_id: Any, **params: Any):
        """调用因子 IC 汇总指标接口。

        请求参数:
            factor_id: 因子 ID。
            **params: ic_scope、time_window 等查询参数。
        返回值:
            因子 IC 汇总指标接口 requests.Response 对象。
        """
        return self.get(f"/api/v1/factor-ic/factors/{factor_id}/summary", params)

    def get_factor_symbol_window_metrics(self, factor_id: Any, **params: Any):
        """调用因子交易对窗口指标接口。

        请求参数:
            factor_id: 因子 ID。
            **params: universe_key、limit 等查询参数。
        返回值:
            因子交易对窗口指标接口 requests.Response 对象。
        """
        return self.get(f"/api/v1/factor-ic/factors/{factor_id}/symbol-window-metrics", params)

    def get_sub_factor_by_symbol(self, sub_factor_id: Any, **params: Any):
        """调用子因子 IC by-symbol 旧汇总接口。

        请求参数:
            sub_factor_id: 子因子 ID。
            **params: ic_scope、time_window 等查询参数。
        返回值:
            子因子 IC by-symbol 接口 requests.Response 对象。
        """
        return self.get(f"/api/v1/factor-ic/sub-factors/{sub_factor_id}/by-symbol", params)

    def get_sub_factor_slice_metrics(self, sub_factor_id: Any, **params: Any):
        """调用子因子 IC 切片指标接口。

        请求参数:
            sub_factor_id: 子因子 ID。
            **params: ic_scope、symbol 等查询参数。
        返回值:
            子因子 IC 切片指标接口 requests.Response 对象。
        """
        return self.get(f"/api/v1/factor-ic/sub-factors/{sub_factor_id}/slice-metrics", params)

    def get_sub_factor_summary(self, sub_factor_id: Any, **params: Any):
        """调用子因子 IC 汇总指标接口。

        请求参数:
            sub_factor_id: 子因子 ID。
            **params: ic_scope、time_window 等查询参数。
        返回值:
            子因子 IC 汇总指标接口 requests.Response 对象。
        """
        return self.get(f"/api/v1/factor-ic/sub-factors/{sub_factor_id}/summary", params)

    def get_sub_factor_symbol_window_metrics(self, sub_factor_id: Any, **params: Any):
        """调用子因子交易对窗口指标接口。

        请求参数:
            sub_factor_id: 子因子 ID。
            **params: universe_key、limit 等查询参数。
        返回值:
            子因子交易对窗口指标接口 requests.Response 对象。
        """
        return self.get(f"/api/v1/factor-ic/sub-factors/{sub_factor_id}/symbol-window-metrics", params)

    def list_summary_metrics(self, **params: Any):
        """调用 IC 汇总指标列表接口。

        请求参数:
            **params: ic_scope、is_sub_factor_id、factor_id、time_window、limit 等查询参数。
        返回值:
            IC 汇总指标列表接口 requests.Response 对象。
        """
        return self.get("/api/v1/factor-ic/summary-metrics", params)

    def batch_upsert_summary_metrics(self, items: list[dict[str, Any]]):
        """调用批量 upsert IC 汇总指标接口。

        请求参数:
            items: 待写入的 IC 汇总指标列表。
        返回值:
            批量 upsert IC 汇总指标接口 requests.Response 对象。
        """
        return self.post("/api/v1/factor-ic/summary-metrics/batch", json={"metrics": items})

    def list_slice_metrics(self, **params: Any):
        """调用 IC 切片指标列表接口。

        请求参数:
            **params: factor_id、is_sub_factor_id、ic_scope、symbol、limit 等查询参数。
        返回值:
            IC 切片指标列表接口 requests.Response 对象。
        """
        return self.get("/api/v1/factor-ic/slice-metrics", params)

    def batch_upsert_slice_metrics(self, items: list[dict[str, Any]]):
        """调用批量 upsert IC 切片指标接口。

        请求参数:
            items: 待写入的 IC 切片指标列表。
        返回值:
            批量 upsert IC 切片指标接口 requests.Response 对象。
        """
        return self.post("/api/v1/factor-ic/slice-metrics/batch", json={"metrics": items})

    def list_runs(self, **params: Any):
        """调用 IC 评估运行记录列表接口。

        请求参数:
            **params: factor_id、limit 等查询参数。
        返回值:
            IC 评估运行记录列表接口 requests.Response 对象。
        """
        return self.get("/api/v1/factor-ic/runs", params)

    def create_run(self, payload: dict[str, Any]):
        """调用创建 IC 评估运行记录接口。

        请求参数:
            payload: 创建 IC run 的 JSON body。
        返回值:
            创建 IC run 接口 requests.Response 对象。
        """
        return self.post("/api/v1/factor-ic/runs", json=payload)

    def get_run(self, run_id: Any):
        """调用 IC 评估运行记录详情接口。

        请求参数:
            run_id: IC run ID。
        返回值:
            IC run 详情接口 requests.Response 对象。
        """
        return self.get(f"/api/v1/factor-ic/runs/{run_id}")

    def list_scoring_standards(self, **params: Any):
        """调用 IC 评分标准列表接口。

        请求参数:
            **params: time_window、coin_category 等查询参数。
        返回值:
            IC 评分标准列表接口 requests.Response 对象。
        """
        return self.get("/api/v1/factor-ic/scoring-standards", params)

    @staticmethod
    def clean_params(params: dict[str, Any] | None) -> dict[str, Any]:
        """过滤查询参数中的 None 值。

        请求参数:
            params: 原始查询参数字典。
        返回值:
            去掉 None 值后的查询参数字典；输入为空时返回空字典。
        """
        if not params:
            return {}
        return {key: value for key, value in params.items() if value is not None}
