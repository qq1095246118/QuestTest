"""动态、可清理测试数据的通用生成工具。"""

from __future__ import annotations

from uuid import uuid4


class TestDataFactory:
    """生成带明确前缀的临时测试数据。"""

    @staticmethod
    def unique_name(prefix: str = "autotest") -> str:
        """生成一个便于定位和清理的唯一名称。

        参数 ``prefix`` 是业务可识别的测试数据前缀。
        返回由前缀和 UUID 组成的字符串；前缀为空白时抛出 ``ValueError``。
        """

        normalized_prefix = prefix.strip()
        if not normalized_prefix:
            raise ValueError("Test data prefix cannot be blank")
        return f"{normalized_prefix}-{uuid4().hex}"
