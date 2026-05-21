"""DB accuracy 表规格加载与解析服务。

本模块负责把 YAML 表配置转换为服务层可使用的数据结构，并校验 DB 字段是否满足对账要求。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from services.db_accuracy.models import ResolvedTableSpec, TableSpec


SPEC_PATH = Path(__file__).resolve().parents[2] / "data/binance_db_accuracy_tables.yaml"


def _as_tuple(value: Any, *, field_name: str, table: str) -> tuple[str, ...]:
    if not value:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{table} field {field_name} must be a list")
    return tuple(str(item) for item in value)


def load_table_specs(path: Path = SPEC_PATH) -> list[TableSpec]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    specs: list[TableSpec] = []
    for item in raw["tables"]:
        table = str(item["table"])
        specs.append(
            TableSpec(
                table=table,
                kind=str(item["kind"]),
                endpoint=str(item["endpoint"]),
                key_fields=_as_tuple(item.get("key_fields"), field_name="key_fields", table=table),
                time_fields=_as_tuple(item.get("time_fields"), field_name="time_fields", table=table),
                interval_field=item.get("interval_field"),
                compare_fields=_as_tuple(
                    item.get("compare_fields"),
                    field_name="compare_fields",
                    table=table,
                ),
                request_limit=int(item.get("request_limit", 1000)),
                optional_compare_fields=_as_tuple(
                    item.get("optional_compare_fields"),
                    field_name="optional_compare_fields",
                    table=table,
                ),
                fixed_interval=item.get("fixed_interval"),
                contract_type_field=item.get("contract_type_field"),
                pair_field=item.get("pair_field"),
                symbol_field=item.get("symbol_field", "symbol"),
                source_time_field=item.get("source_time_field"),
            )
        )
    return specs


def resolve_spec(spec: TableSpec, columns: set[str]) -> ResolvedTableSpec:
    missing_key_fields = [field for field in spec.key_fields if field not in columns]
    if missing_key_fields:
        raise ValueError(f"{spec.table} missing key fields: {missing_key_fields}")

    time_field = None
    for candidate in spec.time_fields:
        if candidate in columns:
            time_field = candidate
            break

    if spec.kind != "registry" and time_field is None:
        raise ValueError(f"{spec.table} has no configured time field in DB columns")

    interval_field = spec.interval_field if spec.interval_field in columns else None
    if spec.interval_field and interval_field is None and not spec.fixed_interval:
        raise ValueError(f"{spec.table} missing interval field: {spec.interval_field}")

    missing_compare_fields = [field for field in spec.compare_fields if field not in columns]
    if missing_compare_fields:
        raise ValueError(f"{spec.table} missing compare fields: {missing_compare_fields}")

    optional_compare_fields = tuple(field for field in spec.optional_compare_fields if field in columns)
    compare_fields = spec.compare_fields + optional_compare_fields

    return ResolvedTableSpec(
        spec=spec,
        columns=tuple(sorted(columns)),
        time_field=time_field,
        interval_field=interval_field,
        compare_fields=compare_fields,
        key_fields=spec.key_fields,
    )
