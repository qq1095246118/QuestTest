"""按接口实际 fetched_at 读取同批原始监控输入，不读取汇总输出作为预期。"""

from __future__ import annotations

import json
from typing import Any

from db.client import DatabaseClient


class StrategyManagementSourceRepository:
    """元数据先限量、再取精确批次的 JSON，避免大数据排序。"""

    def __init__(self, client: DatabaseClient) -> None:
        """保存只读客户端；构造不执行 SQL。"""
        self.client = client

    def source(self, fetched_at: str) -> dict[str, Any]:
        """返回精确同代次的原始输入；找不到时明确报取样缺口。"""
        rows = self.client.fetch_all(
            "SELECT id FROM quant_collection_batches WHERE namespace=%s AND status=%s "
            "ORDER BY created_at DESC,id DESC LIMIT 60", ("quant-minute-test", "committed"))
        ids = [row["id"] for row in rows]
        if not ids:
            raise LookupError("SOURCE_GENERATION_UNAVAILABLE")
        candidates = self.client.fetch_all(
            "SELECT id,JSON_UNQUOTE(JSON_EXTRACT(current_data,'$.account_source.raw.fetched_at')) AS fetched_at "
            "FROM quant_collection_batches WHERE id IN (" + ",".join(["%s"] * len(ids)) + ")", tuple(ids))
        chosen = next((row for row in candidates if row["fetched_at"] == fetched_at), None)
        if chosen is None:
            raise LookupError("SOURCE_GENERATION_UNAVAILABLE: " + fetched_at)
        row = self.client.fetch_one(
            "SELECT JSON_EXTRACT(current_data,'$.account_source') AS source "
            "FROM quant_collection_batches WHERE id=%s", (chosen["id"],))
        source = json.loads(row["source"]) if isinstance(row["source"], str) else row["source"]
        return {"batch_id": chosen["id"], "fetched_at": fetched_at, "source": source}
