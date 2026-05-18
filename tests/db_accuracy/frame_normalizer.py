from __future__ import annotations

from typing import Any

import polars as pl

from tests.db_accuracy.cache_models import MarketShard
from tests.db_accuracy.compare import normalize_value
from tests.db_accuracy.models import SourceRow


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


def _columns(shard: MarketShard) -> tuple[str, ...]:
    return (*shard.key_values.keys(), *shard.compare_fields)


def _normalize_row(shard: MarketShard, row: dict[str, Any]) -> dict[str, str | None]:
    output: dict[str, str | None] = {}
    for field, value in shard.key_values.items():
        output[field] = str(value)
    for field in shard.compare_fields:
        value = row.get(field)
        normalized = normalize_value(value)
        output[field] = _normalized_to_string(normalized)
    return output


def _normalized_to_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, tuple) and len(value) == 4 and value[0] == "decimal":
        _, sign, digits, exponent = value
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
