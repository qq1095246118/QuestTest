"""KB 候选与 universe 的只读实体查询；不引用 API/Service。"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from db.factor4_read_repository import Factor4ReadRepository


@dataclass(frozen=True)
class CandidateTaskSample:
    """一条候选及对应优先活动/否则最新任务；数据不进入默认 repr。"""
    as_of: datetime
    extraction_id: int
    task: dict[str, Any] | None = field(repr=False)


class Factor4AuxiliaryRepository(Factor4ReadRepository):
    """复用只读事务管理，不把数据库结果当原始计算数据。"""

    def candidate_sample(self, *, mapped: bool = False) -> dict[str, Any] | None:
        """返回最新/已映射完整 KB 行或 None；只读字段白名单，连接异常安全透传。"""
        with self._snapshot() as tx:
            return tx.fetch_one("""
                SELECT id, factor_name, validation_status, mapping_status, target_asset_class,
                       confidence_score, updated_at, mapped_factor_id, is_sub_factor_id,
                       pipeline_sub_factor_id
                FROM kb_factor_extractions
                WHERE (%s=0 OR (mapping_status='mapped' AND mapped_factor_id IS NOT NULL))
                ORDER BY updated_at DESC, id DESC LIMIT 1
            """, (int(mapped),))

    def mapped_entity(self, factor_id: int, is_sub_factor: bool) -> dict[str, Any] | None:
        """按类型化映射 ID 返回实体身份或 None；仅内部白名单决定表名，DB 错误透传。"""
        table, name = ("sub_factors", "sub_factor_name") if is_sub_factor else ("factors", "factor_name")
        with self._snapshot() as tx:
            return tx.fetch_one(f"SELECT id, {name} AS name, serial_number FROM {table} WHERE id=%s", (factor_id,))

    def candidate_task_sample(self, status: str | None) -> CandidateTaskSample | None:
        """发现指定当前任务状态或无任务候选；返回快照/None，读取异常透传，无数据修改。"""
        with self._snapshot() as tx:
            if status is None:
                seed = tx.fetch_one("""
                    SELECT e.id FROM kb_factor_extractions e
                    WHERE NOT EXISTS (SELECT 1 FROM kb_factor_mining_tasks t WHERE t.extraction_id=e.id)
                    ORDER BY e.updated_at DESC, e.id DESC LIMIT 1
                """)
                return CandidateTaskSample(datetime.now(timezone.utc), int(seed["id"]), None) if seed else None
            row = tx.fetch_one("""
                SELECT t.extraction_id, t.id, t.status, t.lease_until, t.attempt_count, t.max_attempts,
                       t.next_retry_at, t.pipeline_run_id, t.result_sub_factor_id, t.result_validity,
                       t.last_error_stage, t.last_error_class, t.last_error_code,
                       t.last_error_message, t.retryable
                FROM kb_factor_mining_tasks t JOIN kb_factor_extractions e ON e.id=t.extraction_id
                WHERE t.status=%s AND t.id=(
                    SELECT t2.id FROM kb_factor_mining_tasks t2 WHERE t2.extraction_id=e.id
                    ORDER BY CASE WHEN t2.status IN ('claimed','running') THEN 0 ELSE 1 END,
                             t2.updated_at DESC, t2.id DESC LIMIT 1
                )
                ORDER BY t.updated_at DESC, t.id DESC LIMIT 1
            """, (status,))
            return CandidateTaskSample(datetime.now(timezone.utc), int(row["extraction_id"]), row) if row else None

    def universe_rows(self) -> tuple[dict[str, Any], ...]:
        """返回各集合全部成员和生效边界；空库返回空元组，连接异常安全透传。"""
        with self._snapshot() as tx:
            return tuple(tx.fetch_all("""
                SELECT universe_key, symbol, base_asset, quote_asset, market, exchange_name,
                       instrument_type, sort_order, is_active, valid_from, valid_to
                FROM coin_universe_symbols
                ORDER BY universe_key, sort_order, symbol
            """))

    def detail_entities(self, kind: str, *, limit: int = 50) -> tuple[dict[str, Any], ...]:
        """返回有界实体身份用于详情批量 DB 对账；非法 kind/limit 抛 ValueError，DB 错误透传。"""
        if kind not in {"factor", "sub_factor"} or not 1 <= limit <= 50:
            raise ValueError("unsupported detail entity selection")
        table, name = ("sub_factors", "sub_factor_name") if kind == "sub_factor" else ("factors", "factor_name")
        with self._snapshot() as tx:
            return tuple(tx.fetch_all(f"SELECT id, {name} AS name, serial_number, cn_name FROM {table} ORDER BY id LIMIT %s", (limit,)))

    def parent_children(self, *, maximum_page: bool = False) -> tuple[int, tuple[int, ...]] | None:
        """发现可跨页母子关系集合；普通 3..20，最大页 201..400，缺数据 None，DB 错误透传。"""
        low, high = (201, 400) if maximum_page else (3, 20)
        with self._snapshot() as tx:
            parent = tx.fetch_one("""
                SELECT r.factor_id FROM factor_sub_factor_relations r JOIN factors f ON f.id=r.factor_id
                GROUP BY r.factor_id HAVING COUNT(DISTINCT r.sub_factor_id) BETWEEN %s AND %s
                ORDER BY COUNT(DISTINCT r.sub_factor_id), r.factor_id LIMIT 1
            """, (low, high))
            if parent is None:
                return None
            rows = tx.fetch_all("SELECT DISTINCT sub_factor_id FROM factor_sub_factor_relations WHERE factor_id=%s ORDER BY sub_factor_id", (parent["factor_id"],))
            return int(parent["factor_id"]), tuple(int(row["sub_factor_id"]) for row in rows)

    def overlapping_id_entities(self) -> tuple[tuple[str, dict[str, Any]], ...]:
        """发现母/子共用数值 ID 的两个独立实体；缺样本空元组，DB 异常透传。"""
        with self._snapshot() as tx:
            row = tx.fetch_one("SELECT f.id FROM factors f JOIN sub_factors s ON s.id=f.id ORDER BY f.id LIMIT 1")
            if row is None:
                return ()
            parent = tx.fetch_one("SELECT id, factor_name AS name, serial_number, cn_name FROM factors WHERE id=%s", (row["id"],))
            child = tx.fetch_one("SELECT id, sub_factor_name AS name, serial_number, cn_name FROM sub_factors WHERE id=%s", (row["id"],))
            return (("factor", parent), ("sub_factor", child))

    def summary_by_id(self, summary_id: int) -> dict[str, Any] | None:
        """精确读取公开返回的 summary 及其 Run 状态，不改变因子、维度或 Run。

        输入为正整数 summary 主键；返回完整 DB 行及只用于取样的 _sample_run_status，缺行返回 None。
        关联 Run 缺失时状态为 None，未完成状态原样保留供调用方分类；不做样本选择或历史数量过滤。
        非法主键抛 ValueError，数据库异常由只读事务安全透传，不执行写入。
        """
        if isinstance(summary_id, bool) or not isinstance(summary_id, int) or summary_id <= 0:
            raise ValueError("summary id must be a positive integer")
        with self._snapshot() as tx:
            return tx.fetch_one("""SELECT m.*, r.status AS _sample_run_status
                FROM factor_ic_summary_metrics m LEFT JOIN factor_ic_runs r ON r.run_id=m.run_id
                WHERE m.id=%s LIMIT 1""", (summary_id,))

    def compact_time_summary_sample(self, ic_scope: str) -> dict[str, Any] | None:
        """有界发现全历史 summary <=20、payload 含 period 的真实 completed TS/CS 样本。

        输入 ``ic_scope`` 仅支持 TS/CS；同一只读快照按完成时间分批检查最多 256 个 Run、每 Run
        128 条候选身份和 256 个去重因子，按类型化因子索引最多读取 21 条全历史记录判定资格。
        返回任意符合原资格的完整 summary，不承诺全库最新；只有完整穷尽候选时才返回 None。
        非法 scope 抛 ValueError；预算截断且未找到样本抛带 BLOCKED_SAMPLE_DISCOVERY 的 RuntimeError，
        不将未完成发现误报为缺数据。数据库错误由只读事务安全透传。
        """
        if ic_scope not in {"time_series", "cross_sectional"}:
            raise ValueError("unsupported summary ic scope")
        run_batch_size, maximum_batches = 32, 8
        candidate_limit, maximum_factors = 128, 256
        seen_factors: set[tuple[int, int]] = set()
        cursor: dict[str, Any] | None = None
        runs_checked = 0
        runs_exhausted = False
        candidates_truncated = False
        factor_budget_exhausted = False
        with self._snapshot() as tx:
            for _ in range(maximum_batches):
                continuation, parameters = "", []
                if cursor is not None:
                    if cursor["completed_at"] is None:
                        continuation = "AND completed_at IS NULL AND id < %s"
                        parameters = [cursor["id"]]
                    else:
                        continuation = """AND (completed_at < %s OR
                            (completed_at = %s AND id < %s) OR completed_at IS NULL)"""
                        parameters = [cursor["completed_at"], cursor["completed_at"], cursor["id"]]
                runs = tx.fetch_all(f"""
                    SELECT id, run_id, completed_at FROM factor_ic_runs
                    WHERE status='completed' {continuation}
                    ORDER BY completed_at DESC, id DESC LIMIT %s
                """, (*parameters, run_batch_size))
                if not runs:
                    runs_exhausted = True
                    break
                for run in runs:
                    runs_checked += 1
                    # The run_id index bounds discovery; the extra row detects
                    # truncation without aggregating millions of summaries.
                    candidates = tx.fetch_all("""
                        SELECT factor_id, is_sub_factor_id FROM factor_ic_summary_metrics
                        WHERE run_id=%s AND ic_scope=%s
                        ORDER BY factor_id, is_sub_factor_id LIMIT %s
                    """, (run["run_id"], ic_scope, candidate_limit + 1))
                    candidates_truncated |= len(candidates) > candidate_limit
                    for candidate in candidates[:candidate_limit]:
                        identity = (int(candidate["factor_id"]), int(candidate["is_sub_factor_id"]))
                        if identity in seen_factors:
                            continue
                        if len(seen_factors) >= maximum_factors:
                            factor_budget_exhausted = True
                            break
                        seen_factors.add(identity)
                        history = tx.fetch_all("""
                            SELECT id FROM factor_ic_summary_metrics
                            WHERE factor_id=%s AND is_sub_factor_id=%s LIMIT 21
                        """, identity)
                        if len(history) > 20:
                            continue
                        sample = tx.fetch_one("""
                            SELECT m.* FROM factor_ic_summary_metrics m
                            JOIN factor_ic_runs r ON r.run_id=m.run_id AND r.status='completed'
                            WHERE m.factor_id=%s AND m.is_sub_factor_id=%s AND m.ic_scope=%s
                              AND JSON_UNQUOTE(JSON_EXTRACT(m.metrics_json,'$.summary.period_start')) IS NOT NULL
                              AND JSON_UNQUOTE(JSON_EXTRACT(m.metrics_json,'$.summary.period_end')) IS NOT NULL
                            ORDER BY r.completed_at DESC, m.updated_at DESC, m.id DESC LIMIT 1
                        """, (*identity, ic_scope))
                        if sample is not None:
                            return sample
                    if factor_budget_exhausted:
                        break
                if factor_budget_exhausted:
                    break
                if len(runs) < run_batch_size:
                    runs_exhausted = True
                    break
                cursor = runs[-1]
        if not runs_exhausted or candidates_truncated or factor_budget_exhausted:
            raise RuntimeError(
                f"BLOCKED_SAMPLE_DISCOVERY: compact {ic_scope} sample search incomplete; "
                f"checked_runs={runs_checked}, checked_factors={len(seen_factors)}, "
                f"candidate_rows_truncated={candidates_truncated}, factor_budget_exhausted={factor_budget_exhausted}"
            )
        return None

    def published_cost_results(self) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
        """返回全部 active published 指标与路由的费用投影，不传输无关的大 JSON。

        无输入；同一只读快照保留所有行和身份列，仅提取费用三个字段、metric_ids 及 TS/CS 费用证据。
        JSON 路径存在性单独读取，保留字段缺失、显式 null 与非对象 scope 的区别；空元组代表无数据。
        数据库或 JSON 解码异常由只读事务安全透传，不触发任何写操作。
        """
        fields = ("turnover_rate", "net_return", "sharpe")
        scopes = ("time_series", "cross_sectional")

        def projection(column: str, path: str, alias: str) -> str:
            return (
                f"JSON_CONTAINS_PATH({column}, 'one', '{path}') AS {alias}_present, "
                f"JSON_EXTRACT({column}, '{path}') AS {alias}_value"
            )

        def take(row: dict[str, Any], alias: str) -> tuple[bool, Any]:
            present = bool(row.pop(f"{alias}_present"))
            value = row.pop(f"{alias}_value")
            return present, json.loads(value) if isinstance(value, (str, bytes)) else value

        metric_projection = ", ".join(
            projection("m.metric_payload", f"$.{key}", f"cost_{key}") for key in fields
        )
        route_projection = [projection("r.evidence", "$.metric_ids", "cost_metric_ids")]
        for scope in scopes:
            route_projection.extend((
                f"JSON_CONTAINS_PATH(r.evidence, 'one', '$.{scope}') AS cost_{scope}_present",
                f"JSON_TYPE(JSON_EXTRACT(r.evidence, '$.{scope}')) AS cost_{scope}_type",
                f"CASE WHEN JSON_TYPE(JSON_EXTRACT(r.evidence, '$.{scope}'))='OBJECT' "
                f"THEN NULL ELSE JSON_EXTRACT(r.evidence, '$.{scope}') END AS cost_{scope}_value",
                *(projection("r.evidence", f"$.{scope}.{key}", f"cost_{scope}_{key}") for key in fields),
            ))
        with self._snapshot() as tx:
            metrics = tx.fetch_all(f"""
                SELECT m.id,m.eval_batch_id,m.factor_ref,m.label_code,m.market_scope,m.evaluation_type,
                       m.metric_status,m.is_valid,m.turnover_rate,m.net_return,m.sharpe,{metric_projection}
                FROM market_environment_factor_metric m JOIN market_environment_eval_batch b ON b.id=m.eval_batch_id
                WHERE b.is_active=1 AND b.publish_status='published' ORDER BY m.id
            """)
            routes = tx.fetch_all(f"""
                SELECT r.id,r.eval_batch_id,r.metric_id,r.factor_ref,r.label_code,r.market_scope,
                       {', '.join(route_projection)}
                FROM market_environment_factor_route r JOIN market_environment_eval_batch b ON b.id=r.eval_batch_id
                WHERE b.is_active=1 AND b.publish_status='published' ORDER BY r.id
            """)
            for row in metrics:
                payload: dict[str, Any] = {}
                for key in fields:
                    present, value = take(row, f"cost_{key}")
                    if present:
                        payload[key] = value
                row["metric_payload"] = payload
            for row in routes:
                evidence: dict[str, Any] = {}
                present, value = take(row, "cost_metric_ids")
                if present:
                    evidence["metric_ids"] = value
                for scope in scopes:
                    kind = row.pop(f"cost_{scope}_type")
                    present, value = take(row, f"cost_{scope}")
                    nested: dict[str, Any] = {}
                    for key in fields:
                        field_present, field_value = take(row, f"cost_{scope}_{key}")
                        if field_present:
                            nested[key] = field_value
                    if present:
                        evidence[scope] = nested if kind == "OBJECT" else value
                row["evidence"] = evidence
            return tuple(metrics), tuple(routes)

    def catalog_status_members(self, kind: str, status: str, category: str | None = None) -> tuple[dict[str, Any], ...]:
        """返回状态/可选分类下完整真实目录成员与同状态分类；非法参数ValueError，DB错误安全透传。"""
        states = {"inactive": 0, "new": 1, "valid": 2, "invalid": 3, "deleted": 4}
        if kind not in {"factor", "sub_factor"} or status not in states:
            raise ValueError("invalid catalog status selection")
        table = "sub_factors" if kind == "sub_factor" else "factors"
        name = "sub_factor_name" if kind == "sub_factor" else "factor_name"
        with self._snapshot() as tx:
            return tuple(tx.fetch_all(f"""
                SELECT e.id,e.{name} AS name,e.cn_name,fs.coin_category
                FROM {table} e JOIN factors_status fs ON fs.factor_id=e.id
                WHERE fs.is_sub_factor_id=%s AND fs.status=%s
                  AND (%s IS NULL OR fs.coin_category=%s)
                ORDER BY e.id,fs.coin_category
            """, (int(kind == "sub_factor"), states[status], category, category)))

    def absent_factor_ref(self) -> str:
        """返回读时确认不存在的正整数子因子ref；仅MAX+1识别，无DB写入，错误透传。"""
        with self._snapshot() as tx:
            row = tx.fetch_one("SELECT COALESCE(MAX(id),0)+1000000 AS id FROM sub_factors")
            return f"sub_factor:{row['id']}"

    def absent_candidate_id(self) -> int:
        """返回当前最大候选ID之外的已确认空ID；无写操作，DB错误透传。"""
        with self._snapshot() as tx:
            row = tx.fetch_one("SELECT COALESCE(MAX(id),0)+1000000 AS id FROM kb_factor_extractions")
            return int(row["id"])
