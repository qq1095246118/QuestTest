"""环境读样本兼容已声明的逐因子增量发布，不把未发布或失败批次作为成功样本。"""

from contextlib import contextmanager
import json
import sqlite3
from typing import Any, Iterator

import pytest

from db.client import DatabaseTransaction, QueryParameters
from db.factor4_read_repository import Factor4ReadRepository

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("historical", [False, True])
@pytest.mark.parametrize(
    ("status", "mode", "publish_status", "expected_id"),
    [
        ("success", None, "published", 2),
        ("partial_fail", "per_factor_incremental", "published", 2),
        ("partial_fail", None, "published", 1),
        ("partial_fail", "atomic", "published", 1),
        ("failed", "per_factor_incremental", "published", 1),
        ("running", "per_factor_incremental", "published", 1),
        ("partial_fail", "per_factor_incremental", "pending", 1),
        ("success", None, "pending", 1),
    ],
)
def test_environment_metric_discovery_requires_supported_published_result_state(
    historical: bool, status: str, mode: str | None, publish_status: str, expected_id: int,
) -> None:
    """实际执行SQLite等价只读查询，只有合法新发布可覆盖旧样本，且失败指标不被改写。"""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.create_function("JSON_UNQUOTE", 1, lambda value: value)
    connection.execute("""CREATE TABLE market_environment_eval_batch (
        id INTEGER,batch_uid TEXT,market_scope TEXT,route_profile_key TEXT,publication_uid TEXT,
        publish_version INTEGER,is_active INTEGER,status TEXT,publish_status TEXT,
        score_rule_version TEXT,published_at TEXT,evaluation_config TEXT)""")
    connection.execute("""CREATE TABLE market_environment_factor_metric (
        id INTEGER,eval_batch_id INTEGER,factor_ref TEXT,factor_type TEXT,label_code TEXT,metric_status TEXT)""")
    connection.execute("""CREATE TABLE market_environment_factor_route (
        id INTEGER,eval_batch_id INTEGER,factor_ref TEXT,is_active INTEGER,rank_no INTEGER)""")
    active = int(not historical)
    connection.executemany("INSERT INTO market_environment_eval_batch VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [
        (1,"old","all","default","p1",1,active,"success","published","v1","2026-09-01", "{}"),
        (2,"new","all","default","p2",2,active,status,publish_status,"v1","2026-09-02", json.dumps({"publication_mode": mode})),
        (3,"other-active","all","default","p3",3,1-active,"success","published","v1","2026-09-03", "{}"),
    ])
    connection.executemany("INSERT INTO market_environment_factor_metric VALUES (?,?,?,?,?,?)", [
        (1,1,"sub_factor:1","sub_factor","WIDE_RANGE","success"),
        (2,2,"sub_factor:2","sub_factor","WIDE_RANGE","failed"),
        (3,3,"sub_factor:3","sub_factor","WIDE_RANGE","success"),
    ])

    class Transaction(DatabaseTransaction):
        def fetch_one(self, query: str, parameters: QueryParameters | None = None) -> dict[str, Any] | None:
            """替换绑定占位符后实际执行读取；返回字段字典或None，SQL异常透传。"""
            return super().fetch_one(query.replace("%s", "?"), parameters)

        def fetch_all(self, query: str, parameters: QueryParameters | None = None) -> list[dict[str, Any]]:
            """替换绑定占位符后实际执行读取；返回全部行，SQL异常透传。"""
            return super().fetch_all(query.replace("%s", "?"), parameters)

    class Repository(Factor4ReadRepository):
        @contextmanager
        def _snapshot(self) -> Iterator[DatabaseTransaction]:
            yield Transaction(connection, read_only=True)

    try:
        sample = Repository(None).metric_sample("sub_factor", historical=historical)  # type: ignore[arg-type]
        assert sample is not None and sample.batch["id"] == expected_id
        assert sample.factor_ref == f"sub_factor:{expected_id}"
        assert sample.metrics[0]["metric_status"] == ("failed" if expected_id == 2 else "success")
    finally:
        connection.close()
