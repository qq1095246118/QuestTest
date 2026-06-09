"""因子库原始 API 调用封装。

本模块只负责拼接请求参数并发起 HTTP 调用，不做业务判断或断言。
"""

from __future__ import annotations

from typing import Any

from config.settings import settings
from infrastructure.http.http_client import HTTPClient


class FactorLibraryAPI:
    def __init__(self, token: str | None = None):
        self.base_url = settings.base_url.rstrip("/")
        self.headers = {"Content-Type": "application/json"}
        if token is not None:
            self.headers["Authorization"] = f"Bearer {token}"

    def get(self, endpoint: str, params: dict[str, Any] | None = None):
        url = f"{self.base_url}{endpoint}"
        return HTTPClient.request("GET", url, headers=self.headers, params=_clean(params))

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
        return self.get("/api/v1/themes", {"theme_key": theme_key, "theme_name": theme_name})

    def list_factor_theme_tree(self):
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
        return self.get(
            f"/api/v1/factor-ic/factors/{factor_id}/summary",
            {"ic_scope": ic_scope, "time_window": time_window},
        )


def _clean(params: dict[str, Any] | None) -> dict[str, Any]:
    if not params:
        return {}
    return {key: value for key, value in params.items() if value is not None}
