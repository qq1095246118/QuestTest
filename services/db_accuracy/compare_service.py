"""DB 与源数据字段级比较服务。

本模块负责标准化比较值，并生成字段级差异结果。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from services.db_accuracy.models import Difference


def _canonical_decimal(value: Decimal) -> tuple[str, int, tuple[int, ...], int]:
    if not value.is_finite():
        return ("decimal", 0, tuple(ord(char) for char in str(value)), 0)

    sign, digits, exponent = value.as_tuple()
    digits_list = list(digits)
    if exponent > 0:
        digits_list.extend([0] * exponent)
        exponent = 0

    while exponent < 0 and digits_list and digits_list[-1] == 0:
        digits_list.pop()
        exponent += 1

    if not digits_list or all(digit == 0 for digit in digits_list):
        return ("decimal", 0, (0,), 0)

    return ("decimal", sign, tuple(digits_list), exponent)


def normalize_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return _canonical_decimal(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return _canonical_decimal(Decimal(value))
    if isinstance(value, float):
        return _canonical_decimal(Decimal(str(value)))

    text = str(value).strip()
    if text == "":
        return ""

    try:
        return _canonical_decimal(Decimal(text))
    except InvalidOperation:
        return text


def compare_rows(
    table: str,
    key_label: str,
    row_key: Any,
    db_row: dict[str, Any],
    source_row: dict[str, Any],
    fields: tuple[str, ...],
) -> list[Difference]:
    differences: list[Difference] = []
    for field in fields:
        db_missing = field not in db_row
        source_missing = field not in source_row
        if db_missing or source_missing:
            reason = "missing_both_fields"
            if db_missing and not source_missing:
                reason = "missing_db_field"
            elif source_missing and not db_missing:
                reason = "missing_source_field"

            differences.append(
                Difference(
                    table=table,
                    key_label=key_label,
                    row_key=row_key,
                    field=field,
                    db_value=None if db_missing else db_row[field],
                    source_value=None if source_missing else source_row[field],
                    reason=reason,
                )
            )
            continue

        db_value = db_row[field]
        source_value = source_row[field]
        if normalize_value(db_value) != normalize_value(source_value):
            differences.append(
                Difference(
                    table=table,
                    key_label=key_label,
                    row_key=row_key,
                    field=field,
                    db_value=db_value,
                    source_value=source_value,
                    reason="value_mismatch",
                )
            )
    return differences
