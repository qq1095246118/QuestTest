from __future__ import annotations

from typing import Any

import pytest


class FactorMiningDBService:
    """因子挖掘相关只读 DB 查询服务。

    请求参数:
        不需要实例化，直接通过静态方法读取已有因子挖掘执行数据。
    返回值:
        提供通知接口正向用例所需的 run_id 查询能力。
    """

    @staticmethod
    def first_selected_run_id(client: Any) -> str:
        """查询已有 is_selected=true 的因子挖掘 run_id。

        请求参数:
            client: 只读 DB client，需提供 fetch_one 方法。
        返回值:
            可用于通知接口的 run_id；查不到可用数据时跳过当前用例。
        """
        row = client.fetch_one(
            """
            SELECT run_id
            FROM factor_mining_details
            WHERE is_selected = 1
              AND run_id IS NOT NULL
              AND run_id <> ''
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """
        )
        if not row or not row.get("run_id"):
            pytest.skip("factor_mining_details 中没有 is_selected=true 的可用 run_id。")
        return row["run_id"]
