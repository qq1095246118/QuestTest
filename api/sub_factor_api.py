"""因子库子因子查询接口的协议封装。"""

from __future__ import annotations

from urllib.parse import quote

import requests

from api.client import HTTPClient


class SubFactorAPI:
    """封装登记后回查子因子的详情端点。"""

    def __init__(self, client: HTTPClient) -> None:
        """初始化子因子 API。

        参数 ``client`` 必须使用 Factor Backend 基础地址和当前账号 JWT。
        不返回值；详情查询的响应校验由 Service 或测试用例执行。
        """

        self._client = client

    def get_sub_factor(self, sub_factor_id: int, *, ic_mode: str = "timeseries") -> requests.Response:
        """按 ID 查询子因子详情及指定 IC 展示模式的数据。

        参数 ``sub_factor_id`` 是正整数子因子 ID，``ic_mode`` 是接口支持的 IC 模式，例如 ``timeseries``。
        返回原始 HTTP 响应；不会修改子因子或触发刷新任务。
        """

        if isinstance(sub_factor_id, bool) or int(sub_factor_id) <= 0:
            raise ValueError("sub_factor_id must be a positive integer")
        normalized_mode = str(ic_mode).strip()
        if not normalized_mode:
            raise ValueError("ic_mode must not be blank")
        encoded_id = quote(str(int(sub_factor_id)), safe="")
        return self._client.request(
            "GET",
            f"/sub-factors/{encoded_id}",
            params={"ic_mode": normalized_mode},
        )
