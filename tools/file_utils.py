"""文件读取等低业务耦合工具。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class FileUtils:
    """提供测试数据文件的安全读取能力。"""

    @staticmethod
    def load_json_object(path: Path) -> dict[str, Any]:
        """读取一个 JSON 对象文件。

        参数 ``path`` 是 JSON 文件路径。
        返回解析后的字典；文件不存在、JSON 无效或根节点不是对象时抛出异常。
        """

        with path.open("r", encoding="utf-8") as file:
            content = json.load(file)
        if not isinstance(content, dict):
            raise ValueError(f"JSON root must be an object: {path}")
        return content
