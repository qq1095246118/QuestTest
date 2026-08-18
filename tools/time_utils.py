"""时间处理的低业务耦合工具。"""

from __future__ import annotations

from datetime import UTC, datetime


class TimeUtils:
    """统一生成带时区的 UTC 时间。"""

    @staticmethod
    def utc_now_iso8601() -> str:
        """获取当前 UTC 时间的 ISO 8601 字符串。

        不接收参数。
        返回以 ``Z`` 结尾的 UTC 时间字符串，适用于接口请求或测试数据时间戳。
        """

        return datetime.now(UTC).isoformat().replace("+00:00", "Z")
