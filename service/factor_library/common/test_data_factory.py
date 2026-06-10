"""因子库自动化测试数据工厂。

本模块只负责生成当前测试运行可识别、可清理的测试数据。
"""

from __future__ import annotations

from datetime import datetime
from itertools import count


class TestDataFactory:
    """因子库自动化测试数据工厂。

    请求参数:
        run_id: 可选测试运行标识；不传时使用当前时间戳。
    返回值:
        提供名称、邮箱、角色、提示词等自动化数据生成能力。
    """

    __test__ = False

    def __init__(self, run_id: str | None = None):
        """初始化测试数据工厂。

        请求参数:
            run_id: 可选测试运行标识；不传时使用当前时间戳。
        返回值:
            无，实例化后保存 run_id 和递增序号。
        """
        self.run_id = run_id or datetime.now().strftime("%Y%m%d%H%M%S")
        self._counter = count(1)

    def name(self, resource: str, case_id: str) -> str:
        """生成带 auto_test 前缀的唯一名称。

        请求参数:
            resource: 业务资源名称，例如 factor、theme、prompt。
            case_id: 用例或场景标识。
        返回值:
            auto_test_<run_id>_<resource>_<case_id>_<序号> 格式的唯一名称。
        """
        return f"auto_test_{self.run_id}_{resource}_{case_id}_{next(self._counter)}"

    def email(self, case_id: str) -> str:
        """生成测试邮箱。

        请求参数:
            case_id: 用例或场景标识。
        返回值:
            带 auto_test 前缀和 run_id 的 example.com 邮箱。
        """
        return f"{self.name(case_id, 'user')}@example.com"

    def role_name(self, case_id: str) -> str:
        """生成测试角色名称。

        请求参数:
            case_id: 用例或场景标识。
        返回值:
            带 auto_test 前缀和 run_id 的角色名称。
        """
        return self.name("role", case_id)

    def prompt_name(self, case_id: str) -> str:
        """生成测试提示词名称。

        请求参数:
            case_id: 用例或场景标识。
        返回值:
            带 auto_test 前缀和 run_id 的提示词名称。
        """
        return self.name("prompt", case_id)
