"""公式验收断言：缺字段与 null 分开，十进制按明确绝对容差比较。"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any


def assert_formula_values(actual: Any, expected: Any, *, tolerance: Decimal = Decimal("1e-12"), path: str = "result") -> None:
    """逐项检查独立预期，禁止 NaN/Infinity、布尔数值替换和缺字段通过。"""
    if isinstance(expected, Mapping):
        assert isinstance(actual, Mapping), f"{path}: expected object"
        for key, value in expected.items():
            assert key in actual, f"{path}.{key}: missing field (not explicit null)"
            assert_formula_values(actual[key], value, tolerance=tolerance, path=f"{path}.{key}")
        return
    if isinstance(expected, list):
        assert isinstance(actual, list) and len(actual) == len(expected), f"{path}: sequence length differs"
        for index, (a, e) in enumerate(zip(actual, expected, strict=True)):
            assert_formula_values(a, e, tolerance=tolerance, path=f"{path}[{index}]")
        return
    if expected is None or isinstance(expected, bool):
        assert actual is expected, f"{path}: expected {expected!r}, got {actual!r}"
        return
    field = path.rsplit(".", 1)[-1]
    if field == "id" or field.endswith("_id"):
        assert actual == expected, f"{path}: identity differs"
        return
    try:
        expected_number = Decimal(str(expected))
    except (InvalidOperation, ValueError):
        assert actual == expected, f"{path}: expected {expected!r}, got {actual!r}"
        return
    assert not isinstance(actual, bool), f"{path}: boolean cannot replace number"
    try:
        actual_number = Decimal(str(actual))
    except (InvalidOperation, ValueError):
        raise AssertionError(f"{path}: non numeric value {actual!r}") from None
    assert actual_number.is_finite() and expected_number.is_finite(), f"{path}: non-finite number"
    allowed = Decimal(0) if isinstance(expected, int) else tolerance
    assert abs(actual_number - expected_number) <= allowed, f"{path}: expected {expected_number}, got {actual_number}, tolerance={allowed}"
