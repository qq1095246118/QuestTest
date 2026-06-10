"""因子库自动化资源清理跟踪服务。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class TrackedResource:
    """已登记待清理资源。

    请求参数:
        resource_type: 资源类型。
        value: 清理函数需要的资源标识。
        cleanup: 接收 value 并执行清理的回调。
    返回值:
        保存单条待清理资源信息的数据对象。
    """

    resource_type: str
    value: Any
    cleanup: Callable[[Any], Any]


class ResourceTracker:
    """自动化资源清理跟踪器。

    请求参数:
        实例化时不接收参数。
    返回值:
        提供资源登记和逆序清理能力的 tracker 实例。
    """

    def __init__(self):
        """初始化资源清理跟踪器。

        请求参数:
            无。
        返回值:
            无，实例化后持有待清理资源列表。
        """
        self.resources: list[TrackedResource] = []

    def track(self, resource_type: str, value: Any, cleanup: Callable[[Any], Any]) -> None:
        """登记当前测试运行创建的资源。

        请求参数:
            resource_type: 资源类型。
            value: 清理函数需要的资源标识。
            cleanup: 接收 value 并执行清理的回调。
        返回值:
            无，副作用是把资源加入待清理列表。
        """
        self.resources.append(TrackedResource(resource_type=resource_type, value=value, cleanup=cleanup))

    def cleanup_all(self) -> list[dict[str, Any]]:
        """按逆序清理已登记资源。

        请求参数:
            无，使用当前 tracker 内部登记的资源列表。
        返回值:
            清理失败信息列表；副作用是调用每个资源的 cleanup 并清空登记列表。
        """
        errors = []
        while self.resources:
            resource = self.resources.pop()
            try:
                resource.cleanup(resource.value)
            except Exception as exc:
                errors.append(
                    {
                        "resource_type": resource.resource_type,
                        "value": resource.value,
                        "error": str(exc),
                    }
                )
        return errors
