"""数据质量断言基础设施。

本模块提供可复用的数据结构、数值精度和时间戳校验断言。
"""

from jsonschema import validate

def assert_schema(instance: dict, schema: dict):
    """Data Quality: Validate response against JSON Schema"""
    validate(instance=instance, schema=schema)

def assert_string_precision(val_str: str, max_decimals: int):
    """Data Quality: Validate precision of string numbers"""
    if "." in val_str:
        decimals = len(val_str.split(".")[1])
        assert decimals <= max_decimals, f"Precision {decimals} exceeds max allowed {max_decimals} for {val_str}"

def assert_13_digit_timestamp(ts: int):
    """Data Quality: Validate timestamp is 13 digits (milliseconds)"""
    assert len(str(ts)) == 13, f"Timestamp {ts} is not 13 digits"
