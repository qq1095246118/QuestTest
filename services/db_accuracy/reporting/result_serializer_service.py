"""DB accuracy 结果序列化服务。

本模块负责把 direct 和 cached 对账结果转换为 Allure 附件需要的文本和 JSON。
"""

from __future__ import annotations

import json
from dataclasses import asdict

from services.db_accuracy.cached.cache_models import CachedRunResult
from services.db_accuracy.models import AccuracyRunResult


class ResultSerializerService:
    """将 DB accuracy 服务结果转换为文本摘要和 JSON 明细。"""

    @staticmethod
    def direct_to_json(result: AccuracyRunResult) -> str:
        """把 direct 模式结果转换为稳定的 JSON 字符串。"""
        payload = {
            "passed": result.passed,
            "tables": [
                {
                    "table": table.table,
                    "passed": table.passed,
                    "windows_checked": table.windows_checked,
                    "db_rows_checked": table.db_rows_checked,
                    "source_rows_checked": table.source_rows_checked,
                    "differences": [
                        {
                            "table": difference.table,
                            "key_label": difference.key_label,
                            "row_key": difference.row_key,
                            "field": difference.field,
                            "db_value": difference.db_value,
                            "source_value": difference.source_value,
                            "reason": difference.reason,
                        }
                        for difference in table.differences
                    ],
                }
                for table in result.tables
            ],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2, default=str)

    @staticmethod
    def cached_to_json(result: CachedRunResult) -> str:
        """把 cached 模式结果转换为稳定的 JSON 字符串。"""
        return json.dumps(
            {
                "passed": result.passed,
                "summary": result.summary_text(),
                "shards": [asdict(shard) for shard in result.shards],
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
