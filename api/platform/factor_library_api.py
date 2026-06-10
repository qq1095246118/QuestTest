"""因子库原始 API 调用封装。

本模块只负责拼接请求参数并发起 HTTP 调用，不做业务判断或断言。
"""

from __future__ import annotations

from typing import Any

from config.settings import settings
from service.common.http.http_client import HTTPClient


class FactorLibraryAPI:
    """因子库业务接口原始请求封装。

    请求参数:
        实例化时可传入 token，用于访问需要鉴权的接口。
    返回值:
        提供因子、主题、子因子和 IC 汇总接口请求方法的 API 客户端实例。
    """

    def __init__(self, token: str | None = None):
        """初始化因子库业务 API 客户端。

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
        """向因子库后端发送 GET 请求。

        请求参数:
            endpoint: 以 / 开头的接口路径。
            params: 查询参数字典，值为 None 的字段会被过滤。
        返回值:
            requests.Response 对象；HTTPClient 会对 4xx/5xx 抛出 HTTPError。
        """
        url = f"{self.base_url}{endpoint}"
        return HTTPClient.request("GET", url, headers=self.headers, params=self.clean_params(params))

    def list_factors(
        self,
        page: Any = None,
        limit: Any = None,
        factor_theme: Any = None,
        time_window: Any = None,
        created_by: Any = None,
        created_from: Any = None,
        created_to: Any = None,
        operator_by: Any = None,
        operated_from: Any = None,
        operated_to: Any = None,
        status: Any = None,
        factor_detail_status: Any = None,
        sort_by: Any = None,
        sort_order: Any = None,
    ):
        """调用因子列表接口。

        请求参数:
            page、limit: 分页参数。
            factor_theme、time_window、created_by、created_from、created_to、operator_by、operated_from、operated_to、status、factor_detail_status: 业务筛选参数。
            sort_by、sort_order: 排序字段和排序方向。
        返回值:
            因子列表接口 requests.Response 对象。
        """
        return self.get(
            "/api/v1/factors",
            {
                "page": page,
                "limit": limit,
                "factor_theme": factor_theme,
                "time_window": time_window,
                "created_by": created_by,
                "created_from": created_from,
                "created_to": created_to,
                "operator_by": operator_by,
                "operated_from": operated_from,
                "operated_to": operated_to,
                "status": status,
                "factor_detail_status": factor_detail_status,
                "sort_by": sort_by,
                "sort_order": sort_order,
            },
        )

    def list_themes(self, theme_key: Any = None, theme_name: Any = None):
        """调用主题列表接口。

        请求参数:
            theme_key: 主题稳定标识筛选值。
            theme_name: 主题名称筛选值。
        返回值:
            主题列表接口 requests.Response 对象。
        """
        return self.get("/api/v1/themes", {"theme_key": theme_key, "theme_name": theme_name})

    def list_factor_theme_tree(self):
        """调用因子主题树接口。

        请求参数:
            无。
        返回值:
            因子主题树接口 requests.Response 对象。
        """
        return self.get("/api/v1/factors/theme-tree")

    def list_sub_factors(
        self,
        page: Any = None,
        limit: Any = None,
        sub_factor_name: Any = None,
        factor_id: Any = None,
        factor_detail_status: Any = None,
        sort_by: Any = None,
        sort_order: Any = None,
    ):
        """调用子因子列表接口。

        请求参数:
            page、limit: 分页参数。
            sub_factor_name、factor_id、factor_detail_status: 子因子筛选参数。
            sort_by、sort_order: 排序字段和排序方向。
        返回值:
            子因子列表接口 requests.Response 对象。
        """
        return self.get(
            "/api/v1/sub-factors",
            {
                "page": page,
                "limit": limit,
                "sub_factor_name": sub_factor_name,
                "factor_id": factor_id,
                "factor_detail_status": factor_detail_status,
                "sort_by": sort_by,
                "sort_order": sort_order,
            },
        )

    def get_factor_ic_summary(
        self,
        factor_id: int,
        ic_scope: Any = None,
        time_window: Any = None,
    ):
        """调用指定因子的 IC 汇总接口。

        请求参数:
            factor_id: 因子 ID。
            ic_scope: IC 范围筛选值。
            time_window: 时间窗口筛选值。
        返回值:
            因子 IC 汇总接口 requests.Response 对象。
        """
        return self.get(
            f"/api/v1/factor-ic/factors/{factor_id}/summary",
            {"ic_scope": ic_scope, "time_window": time_window},
        )

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
