"""因子库 factor 模块原始 API 调用封装。

本模块只负责拼接请求参数并发起 HTTP 调用，不做业务判断或断言。
"""

from __future__ import annotations

from typing import Any

from config.settings import settings
from service.common.http.http_client import HTTPClient


class FactorAPI:
    """factor、theme、sub-factor 和公共元数据接口原始请求封装。

    请求参数:
        实例化时可传入 token，用于访问需要鉴权的接口。
    返回值:
        提供 factor 模块 HTTP 请求方法的 API 客户端实例。
    """

    def __init__(self, token: str | None = None):
        """初始化 factor 模块 API 客户端。

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

    def put(self, endpoint: str, json: dict[str, Any] | None = None, params: dict[str, Any] | None = None):
        """发送 PUT 请求。

        请求参数:
            endpoint: 以 / 开头的接口路径。
            json: 请求 JSON body。
            params: 查询参数字典，值为 None 的字段会被过滤。
        返回值:
            requests.Response 对象；HTTPClient 会对 4xx/5xx 抛出 HTTPError。
        """
        url = f"{self.base_url}{endpoint}"
        kwargs: dict[str, Any] = {"headers": self.headers}
        if json is not None:
            kwargs["json"] = json
        if params is not None:
            kwargs["params"] = self.clean_params(params)
        return HTTPClient.request("PUT", url, **kwargs)

    def list_factors(self, **params: Any):
        """调用因子列表接口。

        请求参数:
            **params: page、limit、factor_theme、time_window、created_by、status、sort_by、sort_order 等查询参数。
        返回值:
            因子列表接口 requests.Response 对象。
        """
        return self.get("/api/v1/factors", params)

    def create_factor(self, payload: dict[str, Any]):
        """调用创建因子接口。

        请求参数:
            payload: 创建因子的 JSON body。
        返回值:
            创建因子接口 requests.Response 对象。
        """
        return self.post("/api/v1/factors", json=payload)

    def get_factor(self, factor_id: Any):
        """调用因子详情接口。

        请求参数:
            factor_id: 因子 ID。
        返回值:
            因子详情接口 requests.Response 对象。
        """
        return self.get(f"/api/v1/factors/{factor_id}")

    def update_factor(self, factor_id: Any, payload: dict[str, Any]):
        """调用更新因子接口。

        请求参数:
            factor_id: 因子 ID。
            payload: 更新因子的 JSON body。
        返回值:
            更新因子接口 requests.Response 对象。
        """
        return self.put(f"/api/v1/factors/{factor_id}", json=payload)

    def update_factor_status(self, factor_id: Any, status: Any):
        """调用更新因子详情状态接口。

        请求参数:
            factor_id: 因子 ID。
            status: 目标状态值。
        返回值:
            更新因子详情状态接口 requests.Response 对象。
        """
        return self.put(f"/api/v1/factors/{factor_id}/status", json={"status": status})

    def batch_update_factor_status(self, factor_ids: list[Any], status: Any):
        """调用批量更新因子详情状态接口。

        请求参数:
            factor_ids: 因子 ID 列表。
            status: 目标状态值。
        返回值:
            批量更新因子详情状态接口 requests.Response 对象。
        """
        return self.put("/api/v1/factors/status/batch", json={"factor_ids": factor_ids, "status": status})

    def notify_factor_result(self, run_id: Any):
        """调用因子挖掘结果通知接口。

        请求参数:
            run_id: 运行记录 ID。
        返回值:
            因子挖掘结果通知接口 requests.Response 对象。
        """
        headers = dict(self.headers)
        if settings.factor_webhook_secret:
            headers["X-Webhook-Secret"] = settings.factor_webhook_secret
        url = f"{self.base_url}/api/v1/factors/notification"
        return HTTPClient.request("POST", url, headers=headers, json={"run_id": run_id})

    def get_factors_graph(self, type: Any = None, from_date: Any = None, to_date: Any = None):
        """调用因子月度图表汇总接口。

        请求参数:
            type: 汇总类型。
            from_date: 起始日期，会映射为接口查询参数 from。
            to_date: 结束日期，会映射为接口查询参数 to。
        返回值:
            因子月度图表汇总接口 requests.Response 对象。
        """
        return self.get("/api/v1/factors/graph", {"type": type, "from": from_date, "to": to_date})

    def list_factor_theme_tree(self, factor_theme: Any = None):
        """调用因子主题树接口。

        请求参数:
            factor_theme: 可选主题筛选值。
        返回值:
            因子主题树接口 requests.Response 对象。
        """
        return self.get("/api/v1/factors/theme-tree", {"factor_theme": factor_theme})

    def list_factor_filter_options(self, status: Any = None):
        """调用因子筛选项接口。

        请求参数:
            status: 可选状态筛选值。
        返回值:
            因子筛选项接口 requests.Response 对象。
        """
        return self.get("/api/v1/factors/filter-options", {"status": status})

    def list_themes(self, **params: Any):
        """调用主题列表接口。

        请求参数:
            **params: theme_key、theme_name 等查询参数。
        返回值:
            主题列表接口 requests.Response 对象。
        """
        return self.get("/api/v1/themes", params)

    def create_theme(self, payload: dict[str, Any]):
        """调用创建主题接口。

        请求参数:
            payload: 创建主题的 JSON body。
        返回值:
            创建主题接口 requests.Response 对象。
        """
        return self.post("/api/v1/themes", json=payload)

    def get_theme(self, theme_id: Any):
        """调用主题详情接口。

        请求参数:
            theme_id: 主题 ID。
        返回值:
            主题详情接口 requests.Response 对象。
        """
        return self.get(f"/api/v1/themes/{theme_id}")

    def update_theme(self, theme_id: Any, payload: dict[str, Any]):
        """调用更新主题接口。

        请求参数:
            theme_id: 主题 ID。
            payload: 更新主题的 JSON body。
        返回值:
            更新主题接口 requests.Response 对象。
        """
        return self.put(f"/api/v1/themes/{theme_id}", json=payload)

    def update_theme_status(self, theme_id: Any, status: Any):
        """调用更新主题状态接口。

        请求参数:
            theme_id: 主题 ID。
            status: 目标主题状态。
        返回值:
            更新主题状态接口 requests.Response 对象。
        """
        return self.put(f"/api/v1/themes/{theme_id}/status", json={"status": status})

    def list_sub_factors(self, **params: Any):
        """调用子因子列表接口。

        请求参数:
            **params: page、limit、sub_factor_name、factor_id、status、sort_by、sort_order 等查询参数。
        返回值:
            子因子列表接口 requests.Response 对象。
        """
        return self.get("/api/v1/sub-factors", params)

    def create_sub_factor(self, payload: dict[str, Any]):
        """调用创建子因子接口。

        请求参数:
            payload: 创建子因子的 JSON body。
        返回值:
            创建子因子接口 requests.Response 对象。
        """
        return self.post("/api/v1/sub-factors", json=payload)

    def list_sub_factor_summary(self, **params: Any):
        """调用子因子汇总接口。

        请求参数:
            **params: type、from、to、page、limit、sort_by、sort_order 等查询参数。
        返回值:
            子因子汇总接口 requests.Response 对象。
        """
        return self.get("/api/v1/sub-factors/summary", params)

    def get_sub_factors_graph(self, type: Any = None, from_date: Any = None, to_date: Any = None):
        """调用子因子图表汇总接口。

        请求参数:
            type: 汇总类型。
            from_date: 起始日期，会映射为接口查询参数 from。
            to_date: 结束日期，会映射为接口查询参数 to。
        返回值:
            子因子图表汇总接口 requests.Response 对象。
        """
        return self.get("/api/v1/sub-factors/graph", {"type": type, "from": from_date, "to": to_date})

    def get_sub_factor(self, sub_factor_id: Any):
        """调用子因子详情接口。

        请求参数:
            sub_factor_id: 子因子 ID。
        返回值:
            子因子详情接口 requests.Response 对象。
        """
        return self.get(f"/api/v1/sub-factors/{sub_factor_id}")

    def update_sub_factor(self, sub_factor_id: Any, payload: dict[str, Any]):
        """调用更新子因子接口。

        请求参数:
            sub_factor_id: 子因子 ID。
            payload: 更新子因子的 JSON body。
        返回值:
            更新子因子接口 requests.Response 对象。
        """
        return self.put(f"/api/v1/sub-factors/{sub_factor_id}", json=payload)

    def update_sub_factor_status(self, sub_factor_id: Any, status: Any):
        """调用更新子因子状态接口。

        请求参数:
            sub_factor_id: 子因子 ID。
            status: 目标状态值。
        返回值:
            更新子因子状态接口 requests.Response 对象。
        """
        return self.put(f"/api/v1/sub-factors/{sub_factor_id}/status", json={"status": status})

    def batch_update_sub_factor_status(self, sub_factor_ids: list[Any], status: Any):
        """调用批量更新子因子状态接口。

        请求参数:
            sub_factor_ids: 子因子 ID 列表。
            status: 目标状态值。
        返回值:
            批量更新子因子状态接口 requests.Response 对象。
        """
        return self.put(
            "/api/v1/sub-factors/status/batch",
            json={"sub_factor_ids": sub_factor_ids, "status": status},
        )

    def refresh_sub_factor(self, sub_factor_id: Any):
        """调用创建子因子刷新任务接口。

        请求参数:
            sub_factor_id: 子因子 ID。
        返回值:
            创建子因子刷新任务接口 requests.Response 对象。
        """
        return self.post(f"/api/v1/sub-factors/{sub_factor_id}/refresh")

    def get_sub_factor_refresh(self, sub_factor_id: Any, refresh_id: Any):
        """调用查询子因子刷新任务状态接口。

        请求参数:
            sub_factor_id: 子因子 ID。
            refresh_id: 刷新任务 ID。
        返回值:
            子因子刷新任务状态接口 requests.Response 对象。
        """
        return self.get(f"/api/v1/sub-factors/{sub_factor_id}/refresh/{refresh_id}")

    def copy_sub_factors(self, sub_factor_ids: list[Any]):
        """调用复制子因子接口。

        请求参数:
            sub_factor_ids: 待复制的子因子 ID 列表。
        返回值:
            复制子因子接口 requests.Response 对象。
        """
        return self.post("/api/v1/sub-factors/copy", json={"sub_factor_ids": sub_factor_ids})

    def list_sub_factor_filter_options(self, status: Any = None):
        """调用子因子筛选项接口。

        请求参数:
            status: 可选状态筛选值。
        返回值:
            子因子筛选项接口 requests.Response 对象。
        """
        return self.get("/api/v1/sub-factors/filter-options", {"status": status})

    def get_agent_factory_config(self, coin_category: Any = None):
        """调用 Agent Factory 配置查询接口。

        请求参数:
            coin_category: 可选币种分类。
        返回值:
            Agent Factory 配置查询接口 requests.Response 对象。
        """
        return self.get("/api/v1/agent-factory-config", {"coin_category": coin_category})

    def list_factor_evaluation_standards(self, **params: Any):
        """调用因子评价标准列表接口。

        请求参数:
            **params: time_window、coin_category 等查询参数。
        返回值:
            因子评价标准列表接口 requests.Response 对象。
        """
        return self.get("/api/v1/factor-evaluation-standards", params)

    def copy_factors(self, factor_ids: list[Any]):
        """调用复制因子接口。

        请求参数:
            factor_ids: 待复制的因子 ID 列表。
        返回值:
            复制因子接口 requests.Response 对象。
        """
        return self.post("/api/v1/factors/copy", json={"factor_ids": factor_ids})

    def list_coin_universe_symbols(self, **params: Any):
        """调用币种池交易对列表接口。

        请求参数:
            **params: universe_key、is_active 等查询参数。
        返回值:
            币种池交易对列表接口 requests.Response 对象。
        """
        return self.get("/api/v1/coin-universe-symbols", params)

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
