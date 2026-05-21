"""cached 模式 DB 分区读取服务。

本模块负责发现市场分片，并读取 cached 模式指定时间分区内的 DB 行。
"""

from __future__ import annotations

from typing import Any

from services.db_accuracy.cached.cache_models import MarketShard, TimePartition
from services.db_accuracy.db_reader_service import _dedupe, quote_identifier


class CachedDBReaderService:
    def __init__(self, db: Any):
        self.db = db

    def rows_for_partition(self, shard: MarketShard, partition: TimePartition) -> list[dict[str, Any]]:
        fields = _dedupe((*shard.key_values.keys(), *shard.compare_fields))
        select_fields = ", ".join(quote_identifier(field) for field in fields)
        time_field = quote_identifier(shard.time_field)
        where_parts = [f"{time_field} >= %s", f"{time_field} <= %s"]
        params: list[Any] = [partition.start_ms, partition.end_ms]
        for field, value in shard.key_values.items():
            where_parts.append(f"{quote_identifier(field)} = %s")
            params.append(value)

        sql = (
            f"SELECT {select_fields} "
            f"FROM {quote_identifier(shard.table)} "
            f"WHERE {' AND '.join(where_parts)} "
            f"ORDER BY {time_field} ASC"
        )
        return list(self.db.query(sql, tuple(params)))

    def discover_market_keys(
        self,
        table: str,
        key_fields: tuple[str, ...],
        time_field: str,
        start_ms: int,
        end_ms: int,
        filters: dict[str, Any],
        limit: int,
    ) -> list[dict[str, Any]]:
        selected_keys = ", ".join(quote_identifier(field) for field in key_fields)
        quoted_time_field = quote_identifier(time_field)
        where_parts = [f"{quoted_time_field} >= %s", f"{quoted_time_field} <= %s"]
        params: list[Any] = [start_ms, end_ms]
        for field in key_fields:
            if field in filters:
                where_parts.append(f"{quote_identifier(field)} = %s")
                params.append(filters[field])

        group_by = ", ".join(quote_identifier(field) for field in key_fields)
        sql = (
            f"SELECT {selected_keys} "
            f"FROM {quote_identifier(table)} "
            f"WHERE {' AND '.join(where_parts)} "
            f"GROUP BY {group_by} "
            f"ORDER BY {group_by} "
            f"LIMIT {int(limit)}"
        )
        rows = self.db.query(sql, tuple(params))
        return [{field: row[field] for field in key_fields} for row in rows]
