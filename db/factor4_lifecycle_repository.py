"""Final lifecycle records and bounded audit aggregates; no HTTP or business judgments."""

from dataclasses import dataclass, field
import re
from typing import Any

from db.client import DatabaseClient


@dataclass(frozen=True)
class LifecycleSnapshot:
    """Atomic lifecycle evidence with payloads omitted from representations."""

    batches: tuple[dict[str, Any], ...] = field(repr=False)
    metrics: tuple[dict[str, Any], ...] = field(repr=False)
    routes: tuple[dict[str, Any], ...] = field(repr=False)
    audits: dict[str, dict[str, Any]] = field(repr=False)


_AUDIT_FIELDS = {
    "market_environment_daily": ("request_id", "created_by", "updated_by", "created_at", "updated_at", "schema_version"),
    "market_environment_eval_batch": ("request_id", "created_by", "updated_by", "created_at", "updated_at",
                                      "evaluation_config_version", "score_rule_version", "code_version"),
    "market_environment_factor_metric": ("request_id", "created_by", "updated_by", "created_at", "updated_at", "factor_version", "scoring_version"),
    "market_environment_factor_route": ("request_id", "created_by", "updated_by", "created_at", "updated_at", "factor_version", "score_rule_version", "publish_version"),
}


class Factor4LifecycleRepository:
    """Read lifecycle final state in one test database transaction."""

    def __init__(self, client: DatabaseClient) -> None:
        """Store a gated DB client; perform no I/O or validation side effects."""
        self._client = client

    def snapshot(self) -> LifecycleSnapshot:
        """Return all batch histories and final identities; sanitize DB failures.

        Audit actor/request values are never read, only aggregate missing counts.
        Every table and column identifier is a local whitelist constant.
        """
        try:
            with self._client.transaction() as tx:
                tx.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                tx.execute("SET TRANSACTION READ ONLY")
                tx.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
                try:
                    batches = tx.fetch_all("""
                        SELECT id, batch_uid, idempotency_key, market_scope, route_profile_key,
                               label_kind, start_date, end_date, as_of_time, environment_snapshot,
                               environment_snapshot_hash, factor_set_snapshot_hash, factor_set_snapshot,
                               evaluation_config, evaluation_config_version, score_rule_version,
                               code_version, status, publish_status, publication_uid, publish_version,
                               release_manifest_hash, is_active, active_scope_key, started_at, finished_at,
                               published_at, superseded_at, environment_status,
                               expected_metric_count, completed_metric_count,
                               insufficient_metric_count, failed_metric_count
                        FROM market_environment_eval_batch ORDER BY id
                    """)
                    metrics = tx.fetch_all("""
                        SELECT id, eval_batch_id, factor_ref, factor_type, factor_id, factor_version,
                               market_scope, label_kind, label_code, evaluation_type, metric_status,
                               is_valid, sample_start_date, sample_end_date,
                               `interval`, return_bar_interval, forward_return_bars, window_scope,
                               total_sample_count, valid_sample_count, coverage_rate, mean_ic,
                               mean_rank_ic, icir, rank_icir, t_stat, oos_retention, net_return,
                               sharpe, max_drawdown, turnover_rate, time_series_score,
                               cross_sectional_score, routing_score, confidence,
                               SHA2(CAST(metric_payload AS CHAR), 256) AS payload_hash,
                               JSON_OBJECT('metric_identity', JSON_EXTRACT(metric_payload, '$.metric_identity'),
                                           'sample_day_count', JSON_EXTRACT(metric_payload, '$.sample_day_count'),
                                           'oos', JSON_EXTRACT(metric_payload, '$.oos'),
                                           'direction', JSON_EXTRACT(metric_payload, '$.direction')) AS metric_payload,
                               scoring_version, error_code, created_at, updated_at
                        FROM market_environment_factor_metric ORDER BY id
                    """)
                    routes = tx.fetch_all("SELECT * FROM market_environment_factor_route ORDER BY id")
                    audits = {}
                    for table, fields in _AUDIT_FIELDS.items():
                        columns = ["COUNT(*) AS total", *(f"SUM(`{key}` IS NULL OR CAST(`{key}` AS CHAR)='') AS missing_{key}" for key in fields)]
                        audits[table] = tx.fetch_one(f"SELECT {', '.join(columns)} FROM `{table}`") or {}
                    audits["permission_rejection"] = tx.fetch_one("""
                        SELECT COUNT(*) AS total,
                               SUM(request_id IS NULL OR request_id='') AS missing_request_id,
                               SUM(trace_id IS NULL OR trace_id='') AS missing_trace_id,
                               SUM(caller_subject IS NULL OR caller_subject='') AS missing_actor,
                               SUM(started_at IS NULL) AS missing_started_at,
                               SUM(finished_at IS NULL) AS missing_finished_at
                        FROM agent_data_access_logs WHERE status='failed'
                          AND error_code IN ('INSUFFICIENT_SCOPE', 'INSUFFICIENT_PERMISSION', 'FORBIDDEN')
                    """) or {}
                finally:
                    tx.execute("ROLLBACK")
            return LifecycleSnapshot(tuple(batches), tuple(metrics), tuple(routes), audits)
        except Exception as error:
            raise RuntimeError(f"Lifecycle DB read failed: {type(error).__name__}") from None

    def schema_inventory(self) -> tuple[dict[str, Any], ...]:
        """Return columns and indexes for the five Factor 4.0 entities; DB errors are sanitized."""
        try:
            return tuple(self._client.fetch_all("""
                SELECT c.TABLE_NAME AS table_name, c.COLUMN_NAME AS column_name,
                       s.INDEX_NAME AS index_name, s.NON_UNIQUE AS non_unique,
                       s.SEQ_IN_INDEX AS sequence_no
                FROM information_schema.columns c
                LEFT JOIN information_schema.statistics s
                  ON s.TABLE_SCHEMA=c.TABLE_SCHEMA AND s.TABLE_NAME=c.TABLE_NAME
                 AND s.COLUMN_NAME=c.COLUMN_NAME
                WHERE c.TABLE_SCHEMA=DATABASE() AND c.TABLE_NAME IN (
                  'market_environment_daily', 'market_environment_eval_batch',
                  'market_environment_factor_metric', 'market_environment_factor_route',
                  'market_environment_strategy_feedback_submissions')
                ORDER BY c.TABLE_NAME, c.ORDINAL_POSITION, s.INDEX_NAME
            """))
        except Exception as error:
            raise RuntimeError(f"Lifecycle schema read failed: {type(error).__name__}") from None

    def credential_exposure_counts(self) -> tuple[dict[str, Any], ...]:
        """Count complete MCP-token-shaped values in bounded payload columns, never fetching secrets.

        Large tables are explicit omissions, not clean evidence. Only enumerated
        payload columns and validated schema identifiers are used; DB errors are sanitized.
        """
        try:
            with self._client.transaction() as tx:
                tx.execute("SET TRANSACTION READ ONLY")
                tx.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
                try:
                    columns = tx.fetch_all("""
                        SELECT c.TABLE_NAME AS table_name, c.COLUMN_NAME AS column_name,
                               t.TABLE_ROWS AS approximate_rows
                        FROM information_schema.columns c
                        JOIN information_schema.tables t ON t.TABLE_SCHEMA=c.TABLE_SCHEMA AND t.TABLE_NAME=c.TABLE_NAME
                        WHERE c.TABLE_SCHEMA=DATABASE()
                          AND (c.TABLE_NAME LIKE 'market_environment%%' OR c.TABLE_NAME LIKE 'factor_ic%%'
                            OR c.TABLE_NAME LIKE 'factor_performance%%' OR c.TABLE_NAME LIKE 'factor_validity%%'
                            OR c.TABLE_NAME LIKE 'pipeline_%%' OR c.TABLE_NAME LIKE 'scheduled_job%%'
                            OR c.TABLE_NAME LIKE 'kb_%%' OR c.TABLE_NAME LIKE '%%audit%%' OR c.TABLE_NAME LIKE '%%log%%')
                          AND c.DATA_TYPE IN ('char','varchar','text','mediumtext','longtext','json')
                          AND c.COLUMN_NAME IN ('raw_payload','error_message','message','details','payload',
                                               'request_body','response_body','event_data','metrics_json')
                        ORDER BY c.TABLE_NAME, c.COLUMN_NAME
                    """)
                    result = []
                    for column in columns:
                        table, name = column["table_name"], column["column_name"]
                        if not all(re.fullmatch(r"[A-Za-z0-9_]+", value) for value in (table, name)):
                            raise ValueError("unsafe schema identifier")
                        if int(column.get("approximate_rows") or 0) > 100000:
                            result.append({"table": table, "column": name, "omitted": True})
                            continue
                        count = tx.fetch_one(f"SELECT COUNT(*) AS hits FROM `{table}` WHERE CAST(`{name}` AS CHAR) REGEXP %s",
                                             (r"naf_mcp_[A-Za-z0-9_-]{32,}",)) or {}
                        result.append({"table": table, "column": name, "hits": int(count.get("hits") or 0)})
                finally:
                    tx.execute("ROLLBACK")
            return tuple(result)
        except Exception as error:
            raise RuntimeError(f"Credential exposure scan failed: {type(error).__name__}") from None
