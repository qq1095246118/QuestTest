"""反馈状态与精确请求审计的只读 Repository；不读取原始反馈或密钥内容。"""

from typing import Any

from db.factor4_read_repository import Factor4ReadRepository


class Factor4FeedbackRepository(Factor4ReadRepository):
    """复用强制只读事务，所有标识使用绑定参数。"""

    def caller_for_request(self, request_id: str, tool_name: str) -> dict[str, Any] | None:
        """根据本轮精确 request_id/tool 关联调用者及 scope；无记录 None，DB 错误安全透传。"""
        with self._snapshot() as tx:
            return tx.fetch_one("""
                SELECT l.request_id, l.tool_name, l.caller_subject, l.caller_user_id,
                       l.required_scope, l.status, l.error_code, l.api_key_id,
                       k.owner_user_id, k.subject AS key_subject, k.scopes_json, k.status AS key_status
                FROM agent_data_access_logs l JOIN agent_data_api_keys k ON k.id=l.api_key_id
                WHERE l.request_id=%s AND l.tool_name=%s ORDER BY l.id DESC LIMIT 1
            """, (request_id, tool_name))

    def submission_exists(self, submission_id: str) -> bool:
        """仅确认全库是否有指定业务 ID；返回布尔值，DB 错误安全透传。"""
        with self._snapshot() as tx:
            return tx.fetch_one("SELECT id FROM market_environment_strategy_feedback_submissions WHERE submission_id=%s LIMIT 1", (submission_id,)) is not None

    def submission_samples(self, owner_source: str, *, owned: bool) -> tuple[dict[str, Any], ...]:
        """返回本主体全部或其他主体三条非 payload 状态；无样本空元组，DB 错误透传。"""
        operator, limit = ("=", "") if owned else ("<>", "LIMIT 3")
        with self._snapshot() as tx:
            return tuple(tx.fetch_all(f"""
                SELECT submission_id, source_system, status, accepted_count, rejected_count, error_code
                FROM market_environment_strategy_feedback_submissions
                WHERE source_system {operator} %s ORDER BY id DESC {limit}
            """, (owner_source,)))
