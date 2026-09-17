"""通过实际 SQL 投影验证费用检查不丢失 missing/null/value 语义。"""

from contextlib import contextmanager
from copy import deepcopy
import json
import sqlite3
from typing import Any, Iterator

import pytest

from db.client import DatabaseTransaction
from db.factor4_auxiliary_repository import Factor4AuxiliaryRepository
from service.factor4_cost_result_service import check_published_cost_results

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "mutation", ["values", "missing_metric", "null_metric", "missing_scope_field", "null_scope_field",
                 "missing_scope", "null_scope", "scalar_scope", "missing_ids", "null_ids"],
)
def test_cost_sql_projection_preserves_original_business_issues(mutation: str) -> None:
    """对真实执行的投影 SQL 比较原始断言结果，同时确认不传输无关大字段和未发布行。"""
    values = {"turnover_rate": .3, "net_return": .2, "sharpe": 1.2}
    metric = {
        "id": 1, "eval_batch_id": 2, "factor_ref": "sub_factor:3", "label_code": "WIDE_RANGE",
        "market_scope": "all", "evaluation_type": "time_series", "metric_status": "success",
        "is_valid": 1, **values, "metric_payload": {**values, "unrelated": "x" * 10000},
    }
    route = {
        "id": 4, "eval_batch_id": 2, "metric_id": 1, "factor_ref": "sub_factor:3",
        "label_code": "WIDE_RANGE", "market_scope": "all",
        "evidence": {"metric_ids": {"time_series": 1}, "time_series": {**values, "curve": [1] * 10000}},
    }
    if mutation == "missing_metric":
        del metric["metric_payload"]["net_return"]
    elif mutation == "null_metric":
        metric["metric_payload"]["net_return"] = None
    elif mutation == "missing_scope_field":
        del route["evidence"]["time_series"]["net_return"]
    elif mutation == "null_scope_field":
        route["evidence"]["time_series"]["net_return"] = None
    elif mutation == "missing_scope":
        del route["evidence"]["time_series"]
    elif mutation == "null_scope":
        route["evidence"]["time_series"] = None
    elif mutation == "scalar_scope":
        route["evidence"]["time_series"] = "unexpected"
    elif mutation == "missing_ids":
        del route["evidence"]["metric_ids"]
    elif mutation == "null_ids":
        route["evidence"]["metric_ids"] = None
    original_issues = check_published_cost_results((metric,), (route,)).issues
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    missing = object()

    def extract(document: str | None, path: str) -> Any:
        """模拟 MySQL JSON_EXTRACT 的 JSON 编码返回，缺失路径返回 SQL NULL。"""
        value: Any = json.loads(document) if document is not None else None
        for key in path.removeprefix("$.").split("."):
            value = value.get(key, missing) if isinstance(value, dict) else missing
        return None if value is missing else json.dumps(value)

    def json_type(document: str | None) -> str | None:
        """模拟 MySQL JSON_TYPE；对象与 null 单独区分，其余仅用于保留原标量。"""
        if document is None:
            return None
        value = json.loads(document)
        return "OBJECT" if isinstance(value, dict) else "NULL" if value is None else "SCALAR"

    connection.create_function("JSON_EXTRACT", 2, extract)
    connection.create_function("JSON_TYPE", 1, json_type)
    connection.create_function("JSON_CONTAINS_PATH", 3, lambda doc, mode, path: int(extract(doc, path) is not None))
    connection.execute("CREATE TABLE market_environment_eval_batch(id INTEGER, is_active INTEGER, publish_status TEXT)")
    connection.executemany("INSERT INTO market_environment_eval_batch VALUES (?, ?, ?)", [(2, 1, "published"), (9, 0, "published")])
    for table, source, json_column in (
        ("market_environment_factor_metric", metric, "metric_payload"),
        ("market_environment_factor_route", route, "evidence"),
    ):
        columns = ",".join(source)
        connection.execute(f"CREATE TABLE {table} ({columns})")
        for batch in (2, 9):
            row = deepcopy(source)
            row["eval_batch_id"] = batch
            row[json_column] = json.dumps(row[json_column])
            connection.execute(f"INSERT INTO {table} VALUES ({','.join('?' for _ in row)})", tuple(row.values()))

    class Repository(Factor4AuxiliaryRepository):
        @contextmanager
        def _snapshot(self) -> Iterator[DatabaseTransaction]:
            yield DatabaseTransaction(connection, read_only=True)

    try:
        metrics, routes = Repository(None).published_cost_results()  # type: ignore[arg-type]
        assert len(metrics) == len(routes) == 1
        assert check_published_cost_results(metrics, routes).issues == original_issues
        expected_payload = {key: value for key, value in metric["metric_payload"].items() if key != "unrelated"}
        assert metrics[0]["metric_payload"] == expected_payload
        expected_evidence = deepcopy(route["evidence"])
        if isinstance(expected_evidence.get("time_series"), dict):
            expected_evidence["time_series"].pop("curve", None)
        assert routes[0]["evidence"] == expected_evidence
    finally:
        connection.close()
