"""FactorIC 模块 DB 只读查询服务。"""

from __future__ import annotations

from typing import Any


class FactorICDBQueryService:
    """FactorIC 指标 DB 只读查询服务。

    请求参数:
        不需要实例化，直接通过静态方法接收只读 DB client。
    返回值:
        提供 IC run、slice、summary 和 scoring standards 查询能力。
    """

    @staticmethod
    def fetch_first_factor_id_with_slice(client: Any, is_sub_factor_id: bool) -> int | None:
        """查询首个拥有切片指标的 owner ID。

        请求参数:
            client: 只读 DB client。
            is_sub_factor_id: False 查询母因子，True 查询子因子。
        返回值:
            owner ID；没有数据时返回 None。
        """
        row = client.fetch_one(
            """
            SELECT factor_id
            FROM factor_ic_slice_metrics
            WHERE is_sub_factor_id = %(is_sub_factor_id)s
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            {"is_sub_factor_id": int(is_sub_factor_id)},
        )
        return row["factor_id"] if row else None

    @staticmethod
    def fetch_first_factor_id_with_summary(client: Any, is_sub_factor_id: bool) -> int | None:
        """查询首个拥有汇总指标的 owner ID。

        请求参数:
            client: 只读 DB client。
            is_sub_factor_id: False 查询母因子，True 查询子因子。
        返回值:
            owner ID；没有数据时返回 None。
        """
        row = client.fetch_one(
            """
            SELECT factor_id
            FROM factor_ic_summary_metrics
            WHERE is_sub_factor_id = %(is_sub_factor_id)s
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            {"is_sub_factor_id": int(is_sub_factor_id)},
        )
        return row["factor_id"] if row else None

    @staticmethod
    def fetch_run_by_run_id(client: Any, run_id: str) -> dict[str, Any] | None:
        """按 run_id 查询 IC 运行记录。

        请求参数:
            client: 只读 DB client。
            run_id: 运行记录业务 ID。
        返回值:
            IC run 记录；不存在时返回 None。
        """
        return client.fetch_one(
            """
            SELECT *
            FROM factor_ic_runs
            WHERE run_id = %(run_id)s
            """,
            {"run_id": run_id},
        )
