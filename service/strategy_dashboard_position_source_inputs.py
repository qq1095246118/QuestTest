"""为只关注浮盈/保证金的场景补齐实际持仓端点必需的无关源字段。"""

from __future__ import annotations

from typing import Any


def complete_position_financial_inputs(positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """保留每行 U/margin 原值，只为未提供的数量和名义设置显式固定输入。

    只适用于测试目标为浮盈与保证金求和的场景；数量或名义本身被测时不能
    使用此函数。不会覆盖已有键（含 null）、去重、过滤或计算任何合计。
    """
    result = []
    for row in positions:
        source = dict(row)
        short = row.get("side", row.get("position_side")) == "short"
        source.setdefault("quantity", "-1" if short else "1")
        source.setdefault("notional", "-100" if short else "100")
        result.append(source)
    return result
