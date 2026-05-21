"""cached 模式 DataFrame 归一化服务。

本模块负责把 DB 行和源数据行转换为 DataComPy 可比较的标准 DataFrame。
"""

from __future__ import annotations

from typing import Any

import polars as pl

from services.db_accuracy.cached.cache_models import MarketShard
from services.db_accuracy.compare_service import normalize_value
from services.db_accuracy.models import SourceRow


MISSING_FIELD_SENTINEL = "__DB_ACCURACY_MISSING__"
COMPARE_COLUMN_SUFFIX = "__compare"


class DuplicateJoinKeyError(ValueError):
    pass


def rows_to_normalized_frame(shard: MarketShard, rows: list[dict[str, Any]]) -> pl.DataFrame:
    normalized_rows = [_normalize_row(shard, row) for row in rows]
    frame = pl.DataFrame(
        normalized_rows,
        schema={column: pl.String for column in _columns(shard)},
        orient="row",
    )
    _assert_unique_join_keys(shard, frame)
    return frame


def source_rows_to_normalized_frame(shard: MarketShard, rows: list[SourceRow]) -> pl.DataFrame:
    return rows_to_normalized_frame(shard, [row.fields for row in rows])


def normalized_compare_columns(shard: MarketShard) -> tuple[str, ...]:
    join_columns = set(shard.join_columns)
    return tuple(
        _payload_column(field) if field in join_columns else field
        for field in shard.compare_fields
    )


def _columns(shard: MarketShard) -> tuple[str, ...]:
    return (*shard.join_columns, *normalized_compare_columns(shard))


def _normalize_row(shard: MarketShard, row: dict[str, Any]) -> dict[str, str | None]:
    output: dict[str, str | None] = {}
    for field, value in shard.key_values.items():
        output[field] = str(value)
    output[shard.time_field] = _normalized_to_string(
        normalize_value(row.get(shard.time_field))
    )

    join_columns = set(shard.join_columns)
    for field in shard.compare_fields:
        output_field = _payload_column(field) if field in join_columns else field
        if field in row:
            normalized = normalize_value(row[field])
            output[output_field] = _normalized_to_string(normalized)
        else:
            output[output_field] = MISSING_FIELD_SENTINEL
    return output


def _payload_column(field: str) -> str:
    return f"{field}{COMPARE_COLUMN_SUFFIX}"


def _normalized_to_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, tuple) and len(value) == 4 and value[0] == "decimal":
        _, sign, digits, exponent = value
        if any(not isinstance(digit, int) or digit < 0 or digit > 9 for digit in digits):
            digits_text = ",".join(str(digit) for digit in digits)
            return f"decimal:{sign}:{digits_text}:{exponent}"
        digits_text = "".join(str(digit) for digit in digits)
        if exponent < 0:
            split_at = len(digits_text) + exponent
            if split_at <= 0:
                digits_text = "0." + "0" * abs(split_at) + digits_text
            else:
                digits_text = digits_text[:split_at] + "." + digits_text[split_at:]
        elif exponent > 0:
            digits_text = digits_text + "0" * exponent
        if sign:
            digits_text = "-" + digits_text
        return digits_text
    return str(value)


def _assert_unique_join_keys(shard: MarketShard, frame: pl.DataFrame) -> None:
    if frame.is_empty():
        return
    duplicate_count = (
        frame.group_by(list(shard.join_columns))
        .len()
        .filter(pl.col("len") > 1)
        .height
    )
    if duplicate_count:
        raise DuplicateJoinKeyError(
            f"duplicate join key rows found for {shard.label}: {duplicate_count}"
        )
