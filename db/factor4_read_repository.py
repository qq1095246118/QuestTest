"""历史只读探针的数据发现与实体访问；不依赖 API、Service 或 Case。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

from db.client import DatabaseClient, DatabaseTransaction

Kind = Literal["factor", "sub_factor"]
_TABLES = {"factor": ("factors", "factor_name", 0),
           "sub_factor": ("sub_factors", "sub_factor_name", 1)}
_STATUSES = {0: "inactive", 1: "new", 2: "valid", 3: "invalid", 4: "deleted"}


@dataclass(frozen=True)
class CatalogSubset:
    """一个可完整遍历的目录子集；行数据不进入默认 repr。"""

    kind: Kind
    status: str
    category: str
    rows: tuple[dict[str, Any], ...] = field(repr=False)


@dataclass(frozen=True)
class DailyReadSnapshot:
    """同一只读事务捕获的全部环境修订及固定查询时点。"""

    as_of: datetime
    rows: tuple[dict[str, Any], ...] = field(repr=False)


@dataclass(frozen=True)
class EnvironmentMetricSample:
    """动态发现的单因子/单批次指标与路由；不是原始计算输入。"""

    batch: dict[str, Any] = field(repr=False)
    factor_ref: str
    metrics: tuple[dict[str, Any], ...] = field(repr=False)
    routes: tuple[dict[str, Any], ...] = field(repr=False)


@dataclass(frozen=True)
class EnvironmentReadMatrixSnapshot:
    """One DB instant and representative real entities for every active publication shape."""

    as_of: datetime
    batches: tuple[dict[str, Any], ...] = field(repr=False)
    samples: tuple[EnvironmentMetricSample, ...] = field(repr=False)


SUMMARY_KEYS = (
    "ic_scope", "calculation_mode", "factor_bar_interval", "factor_window_bars",
    "return_bar_interval", "forward_return_bars", "universe_key", "symbol",
    "window_scope", "scoring_version",
)


@dataclass(frozen=True)
class SummarySample:
    """完整小分区的每因子最新汇总与公式证据；不含原始行情。"""

    kind: Kind
    as_of: datetime
    scope: dict[str, Any]
    rows: tuple[dict[str, Any], ...] = field(repr=False)
    formulas: tuple[dict[str, Any], ...] = field(repr=False)


@dataclass(frozen=True)
class ResearchCatalogSnapshot:
    """Current interval catalog joined to completed results at one fixed research scope."""

    sample: SummarySample
    rows: tuple[dict[str, Any], ...] = field(repr=False)
    ambiguous_count: int = 0


@dataclass(frozen=True)
class MetricScopeSnapshot:
    """指定端点筛选下的所有 completed run/factor 记录，保留历史以重建 PIT 并集。"""

    kind: Kind
    as_of: datetime
    filters: dict[str, Any]
    rows: tuple[dict[str, Any], ...] = field(repr=False)

@dataclass(frozen=True)
class ValiditySample:
    """A complete validity row and its referenced TS/CS summaries."""
    validity: dict[str, Any] = field(repr=False)
    summaries: dict[str, dict[str, Any]] = field(repr=False)

@dataclass(frozen=True)
class SliceSample:
    """A metric summary scope and all persisted slice rows for it."""
    summary: dict[str, Any] = field(repr=False)
    rows: tuple[dict[str, Any], ...] = field(repr=False)


class Factor4ReadRepository:
    """通过短生命周期、只读、可重复读事务获取测试 Oracle。"""

    def __init__(self, client: DatabaseClient) -> None:
        """输入测试环境 DB 客户端；不打开连接，不执行 SQL。"""
        self._client = client

    @contextmanager
    def _snapshot(self) -> Iterator[DatabaseTransaction]:
        try:
            with self._client.transaction() as tx:
                tx.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                tx.execute("SET TRANSACTION READ ONLY")
                tx.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
                try:
                    yield tx
                finally:
                    tx.execute("ROLLBACK")
        except Exception as exc:
            # Driver text may contain a connection string or password.
            raise RuntimeError(f"Factor4 read-only database access failed: {type(exc).__name__}") from None

    def catalog_subset(self, kind: Kind) -> CatalogSubset | None:
        """按实体类型发现 4～20 个目录成员的小分区；返回 None 表示无前置，DB 错误安全透传。"""
        if kind not in _TABLES:
            raise ValueError("unsupported catalog kind")
        table, name_field, sub_flag = _TABLES[kind]
        with self._snapshot() as tx:
            subset = tx.fetch_one(f"""
                SELECT fs.status, fs.coin_category, COUNT(DISTINCT e.id) AS n
                FROM factors_status fs JOIN {table} e ON e.id=fs.factor_id
                WHERE fs.is_sub_factor_id=%s AND fs.status BETWEEN 0 AND 4
                  AND fs.coin_category IS NOT NULL AND fs.coin_category<>''
                GROUP BY fs.status, fs.coin_category
                HAVING n BETWEEN 4 AND 20
                ORDER BY ABS(n-8), fs.status, fs.coin_category LIMIT 1
            """, (sub_flag,))
            if subset is None:
                return None
            rows = tx.fetch_all(f"""
                SELECT DISTINCT e.id, e.{name_field} AS name, e.cn_name,
                       e.serial_number, e.data_source, e.updated_at
                FROM {table} e JOIN factors_status fs ON fs.factor_id=e.id
                WHERE fs.is_sub_factor_id=%s AND fs.status=%s AND fs.coin_category=%s
                ORDER BY e.id
            """, (sub_flag, subset["status"], subset["coin_category"]))
            return CatalogSubset(kind, _STATUSES[int(subset["status"])],
                                 str(subset["coin_category"]), tuple(rows))

    def daily_snapshot(self) -> DailyReadSnapshot:
        """读取全部 fact/forecast 修订和 UTC 时点；无数据返回空行，连接/SQL 错误安全透传。"""
        with self._snapshot() as tx:
            clock = tx.fetch_one("SELECT UTC_TIMESTAMP(6) AS as_of_time")
            rows = tx.fetch_all("""
                SELECT id, environment_date, label_kind, label_code, label_status,
                       revision, is_current, available_at, features, probabilities,
                       confidence, confidence_level, effective_from, effective_to,
                       model_version, schema_version, raw_payload,
                       created_at, updated_at, created_by, updated_by, request_id
                FROM market_environment_daily
                ORDER BY environment_date DESC, label_kind, revision DESC, id DESC
            """)
            if clock is None or not isinstance(clock.get("as_of_time"), datetime):
                raise ValueError("database clock missing")
            return DailyReadSnapshot(clock["as_of_time"].replace(tzinfo=timezone.utc), tuple(rows))

    def metric_sample(
        self, kind: Kind, *, historical: bool = False,
        batch_uid: str | None = None, factor_ref: str | None = None,
    ) -> EnvironmentMetricSample | None:
        """Read a representative or an explicit existing factor/batch including empty results.

        Explicit batch_uid/factor_ref must be supplied together; their values are
        bound parameters, and a missing catalog entity/batch returns None. The
        historical discovery behavior is preserved when both are omitted. Invalid
        kind/ref raises ValueError and read-only database failures are sanitized.
        """
        if kind not in _TABLES:
            raise ValueError("unsupported factor kind")
        if (batch_uid is None) != (factor_ref is None):
            raise ValueError("explicit metric sample requires batch_uid and factor_ref")
        if factor_ref is not None:
            prefix, separator, identifier = factor_ref.partition(":")
            if prefix != kind or not separator or not identifier.isdigit() or int(identifier) < 1:
                raise ValueError("explicit metric sample requires a matching positive factor ref")
            with self._snapshot() as tx:
                entity = tx.fetch_one(f"SELECT id FROM {_TABLES[kind][0]} WHERE id=%s", (int(identifier),))
                batch = tx.fetch_one("""SELECT id,batch_uid,market_scope,route_profile_key,publication_uid,
                    publish_version,is_active,status,publish_status,score_rule_version,published_at,
                    as_of_time,label_kind FROM market_environment_eval_batch WHERE batch_uid=%s""", (batch_uid,))
                if entity is None or batch is None:
                    return None
                return self._environment_factor_rows(tx, batch, factor_ref)
        with self._snapshot() as tx:
            batch = tx.fetch_one("""
                SELECT b.id, b.batch_uid, b.market_scope, b.route_profile_key,
                       b.publication_uid, b.publish_version, b.is_active, b.status,
                       b.publish_status, b.score_rule_version, b.published_at
                FROM market_environment_eval_batch b
                WHERE b.is_active=%s AND b.status='success'
                  AND b.publication_uid IS NOT NULL
                  AND EXISTS (SELECT 1 FROM market_environment_factor_metric m
                              WHERE m.eval_batch_id=b.id AND m.factor_type=%s)
                ORDER BY b.published_at DESC, b.id DESC LIMIT 1
            """, (0 if historical else 1, kind))
            if batch is None:
                return None
            factor = tx.fetch_one("""
                SELECT m.factor_ref, COUNT(DISTINCT m.label_code) AS label_count,
                       COUNT(*) AS metric_count,
                       EXISTS (SELECT 1 FROM market_environment_factor_route r
                               WHERE r.eval_batch_id=m.eval_batch_id
                                 AND r.factor_ref=m.factor_ref AND r.is_active=1) AS routed
                FROM market_environment_factor_metric m
                WHERE m.eval_batch_id=%s AND m.factor_type=%s
                GROUP BY m.factor_ref, m.eval_batch_id
                ORDER BY routed DESC, label_count DESC, metric_count DESC, m.factor_ref LIMIT 1
            """, (batch["id"], kind))
            if factor is None:
                return None
            params = (batch["id"], factor["factor_ref"])
            metrics = tx.fetch_all("""
                SELECT * FROM market_environment_factor_metric
                WHERE eval_batch_id=%s AND factor_ref=%s ORDER BY id
            """, params)
            routes = tx.fetch_all("""
                SELECT * FROM market_environment_factor_route
                WHERE eval_batch_id=%s AND factor_ref=%s AND is_active=1 ORDER BY rank_no, id
            """, params)
            return EnvironmentMetricSample(batch, factor["factor_ref"], tuple(metrics), tuple(routes))

    @staticmethod
    def _environment_factor_rows(
        tx: DatabaseTransaction, batch: dict[str, Any], factor_ref: str,
    ) -> EnvironmentMetricSample:
        parameters = (batch["id"], factor_ref)
        metrics = tx.fetch_all("""SELECT * FROM market_environment_factor_metric
            WHERE eval_batch_id=%s AND factor_ref=%s ORDER BY id""", parameters)
        routes = tx.fetch_all("""SELECT * FROM market_environment_factor_route
            WHERE eval_batch_id=%s AND factor_ref=%s AND is_active=1 ORDER BY rank_no,id""", parameters)
        return EnvironmentMetricSample(batch, factor_ref, tuple(metrics), tuple(routes))

    @staticmethod
    def _environment_publication_rows(tx: DatabaseTransaction) -> tuple[dict[str, Any], ...]:
        return tuple(tx.fetch_all("""SELECT id,batch_uid,market_scope,route_profile_key,publication_uid,
            publish_version,is_active,status,publish_status,score_rule_version,published_at,as_of_time,label_kind
            FROM market_environment_eval_batch WHERE is_active=1 ORDER BY market_scope,route_profile_key,id"""))

    def active_environment_publications(self) -> tuple[dict[str, Any], ...]:
        """Read all active publication identities for a bounded before/after drift check.

        Returns an empty tuple when none exist. Database failures are sanitized;
        no assertion, implicit fallback, formula execution or write is performed.
        """
        with self._snapshot() as tx:
            return self._environment_publication_rows(tx)

    def environment_matrix_snapshot(self) -> EnvironmentReadMatrixSnapshot:
        """Discover every active partition and bounded representative factor shapes.

        For each metric kind/label/TS-CS and route kind/label select a real factor
        representative, plus an existing nondeleted entity with no metrics/no
        active routes when available. Read full rows only for the union of those
        representatives in one consistent transaction; absence is retained for
        Service coverage reporting. Empty representatives conservatively require
        each observed catalog category's latest updated_at/id status to be known
        and nondeleted, with at least one such status. No unverified market-to-
        category mapping, fake entities or DB writes are introduced. Missing safe
        representatives are a coverage precondition, not a product defect.
        """
        with self._snapshot() as tx:
            as_of = self._clock(tx)
            batches = self._environment_publication_rows(tx)
            if not batches:
                return EnvironmentReadMatrixSnapshot(as_of, (), ())
            metric_shapes = tx.fetch_all("""SELECT m.eval_batch_id,m.factor_type,m.label_code,m.evaluation_type,
                MIN(m.factor_ref) AS factor_ref FROM market_environment_factor_metric m
                JOIN market_environment_eval_batch b ON b.id=m.eval_batch_id AND b.is_active=1
                GROUP BY m.eval_batch_id,m.factor_type,m.label_code,m.evaluation_type""")
            route_shapes = tx.fetch_all("""SELECT r.eval_batch_id,r.factor_type,r.label_code,MIN(r.factor_ref) AS factor_ref
                FROM market_environment_factor_route r JOIN market_environment_eval_batch b
                  ON b.id=r.eval_batch_id AND b.is_active=1 WHERE r.is_active=1
                GROUP BY r.eval_batch_id,r.factor_type,r.label_code""")
            selected: dict[int, set[str]] = {batch["id"]: set() for batch in batches}
            for row in (*metric_shapes, *route_shapes):
                if row["eval_batch_id"] in selected:
                    selected[row["eval_batch_id"]].add(row["factor_ref"])
            for batch in batches:
                for kind, (table, _, sub_flag) in _TABLES.items():
                    for entity_table, active_clause in (("market_environment_factor_metric", ""),
                                                       ("market_environment_factor_route", " AND e.is_active=1")):
                        empty = tx.fetch_one(f"""SELECT f.id FROM {table} f WHERE f.id IN (
                            SELECT current_status.factor_id FROM (
                                SELECT fs.factor_id,fs.status,ROW_NUMBER() OVER (
                                    PARTITION BY fs.factor_id,fs.coin_category
                                    ORDER BY fs.updated_at DESC,fs.id DESC) AS status_recency
                                FROM factors_status fs WHERE fs.is_sub_factor_id=%s
                            ) current_status WHERE current_status.status_recency=1
                            GROUP BY current_status.factor_id
                            HAVING MIN(CASE WHEN current_status.status BETWEEN 0 AND 3 THEN 1 ELSE 0 END)=1)
                            AND NOT EXISTS (SELECT 1 FROM {entity_table} e WHERE e.eval_batch_id=%s
                              AND e.factor_type=%s AND e.factor_id=f.id{active_clause}) ORDER BY f.id LIMIT 1""",
                                             (sub_flag, batch["id"], kind))
                        if empty:
                            selected[batch["id"]].add(f"{kind}:{empty['id']}")
            samples = tuple(self._environment_factor_rows(tx, batch, ref)
                            for batch in batches for ref in sorted(selected[batch["id"]]))
            return EnvironmentReadMatrixSnapshot(as_of, batches, samples)

    def summary_sample(self, shape: str, kind: Kind = "sub_factor", *, calculation_mode: str | None = None,
                       minimum_factors: int = 3) -> SummarySample | None:
        """发现 TS 币种/TS 汇总/CS 汇总完整小分区；缺前置返回 None，非法形态 ValueError。

        同一只读事务捕获数据库时钟、全候选最新汇总和公式证据；数据库异常安全透传。
        不预先剔除 null 指标，防止把旧 Run 非空值当成最新结果。
        """
        shapes = {"ts_symbol": ("time_series", "m.symbol<>''"),
                  "ts_aggregate": ("time_series", "m.symbol=''"),
                  "cs_aggregate": ("cross_sectional", "m.symbol=''")}
        if shape not in shapes or kind not in _TABLES or calculation_mode not in {None, "direct", "child_aggregate"} or not 1 <= minimum_factors <= 20:
            raise ValueError("unsupported summary shape or kind")
        ic_scope, symbol_clause = shapes[shape]
        sub_flag = _TABLES[kind][2]
        group = ", ".join("m." + key for key in SUMMARY_KEYS)
        with self._snapshot() as tx:
            as_of = self._clock(tx)
            cutoff = as_of.astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
            mode_clause = " AND m.calculation_mode=%s" if calculation_mode is not None else ""
            choice_parameters = (sub_flag, ic_scope, cutoff, *((calculation_mode,) if calculation_mode is not None else ()), minimum_factors)
            scope = tx.fetch_one(f"""
                SELECT {group}, COUNT(DISTINCT m.factor_id) AS factor_count,
                       MAX(r.completed_at) AS latest_completed
                FROM factor_ic_summary_metrics m
                JOIN factor_ic_runs r ON r.run_id=m.run_id AND r.status='completed'
                WHERE m.is_sub_factor_id=%s AND m.ic_scope=%s AND {symbol_clause}
                  AND r.completed_at<=%s {mode_clause}
                GROUP BY {group}
                HAVING factor_count BETWEEN %s AND 20
                   AND COUNT(DISTINCT CASE WHEN m.mean_ic IS NOT NULL
                         AND m.rank_is_icir IS NOT NULL AND m.rank_is_icir<>0
                         THEN m.factor_id END)=factor_count
                ORDER BY factor_count, latest_completed DESC, {group} LIMIT 1
            """, choice_parameters)
            if scope is None:
                return None
            predicate = " AND ".join("m." + key + "=%s" for key in SUMMARY_KEYS)
            params = (sub_flag, *(scope[key] for key in SUMMARY_KEYS), cutoff)
            rows = tx.fetch_all(f"""
                SELECT ranked.* FROM (
                    SELECT m.*, r.completed_at AS run_completed_at,
                           ROW_NUMBER() OVER (PARTITION BY m.factor_id
                               ORDER BY r.completed_at DESC, m.updated_at DESC, m.id DESC) AS rn
                    FROM factor_ic_summary_metrics m
                    JOIN factor_ic_runs r ON r.run_id=m.run_id AND r.status='completed'
                    WHERE m.is_sub_factor_id=%s AND {predicate} AND r.completed_at<=%s
                ) ranked WHERE rn=1 ORDER BY factor_id
            """, params)
            formulas: list[dict[str, Any]] = []
            for row in rows:
                formulas.extend(tx.fetch_all("""
                    SELECT * FROM factor_ic_run_formula_evidence
                    WHERE run_id=%s AND factor_id=%s AND is_sub_factor_id=%s
                      AND calculation_mode=%s AND factor_bar_interval=%s
                      AND factor_window_bars=%s AND return_bar_interval=%s
                      AND forward_return_bars=%s ORDER BY id
                """, (row["run_id"], row["factor_id"], sub_flag, row["calculation_mode"],
                      row["factor_bar_interval"], row["factor_window_bars"],
                      row["return_bar_interval"], row["forward_return_bars"])))
            return SummarySample(kind, as_of, {key: scope[key] for key in SUMMARY_KEYS},
                                 tuple(rows), tuple(formulas))

    def metric_scope_snapshot(self, ic_scope: str, kind: Kind = "sub_factor") -> MetricScopeSnapshot | None:
        """发现端点可筛选的 interval/universe 分区并读取全部历史；无样本 None，非法输入 ValueError。

        优先 scope 较少的分区，让无 cursor 的端点也可全量对账；不使用 GROUP_CONCAT。
        run_completed_at 属于上海生命周期时间，metric_period_end 为 UTC 数据时间。
        """
        if ic_scope not in {"time_series", "cross_sectional"} or kind not in _TABLES:
            raise ValueError("unsupported scope filter")
        sub_flag = _TABLES[kind][2]
        group = ", ".join("m." + key for key in SUMMARY_KEYS)
        with self._snapshot() as tx:
            as_of = self._clock(tx)
            choice = tx.fetch_one(f"""
                SELECT scopes.factor_bar_interval, scopes.universe_key, COUNT(*) AS scope_count
                FROM (
                    SELECT {group} FROM factor_ic_summary_metrics m
                    JOIN factor_ic_runs r ON r.run_id=m.run_id AND r.status='completed'
                    WHERE m.is_sub_factor_id=%s AND m.ic_scope=%s AND r.completed_at IS NOT NULL
                    GROUP BY {group}
                ) scopes
                GROUP BY scopes.factor_bar_interval, scopes.universe_key
                ORDER BY (COUNT(*) BETWEEN 2 AND 100) DESC, COUNT(*),
                         scopes.factor_bar_interval, scopes.universe_key LIMIT 1
            """, (sub_flag, ic_scope))
            if choice is None:
                return None
            rows = tx.fetch_all(f"""
                SELECT {group}, m.factor_id, m.run_id, r.completed_at AS run_completed_at,
                       MAX(m.period_end) AS metric_period_end
                FROM factor_ic_summary_metrics m
                JOIN factor_ic_runs r ON r.run_id=m.run_id AND r.status='completed'
                WHERE m.is_sub_factor_id=%s AND m.ic_scope=%s AND m.factor_bar_interval=%s
                  AND m.universe_key=%s AND r.completed_at IS NOT NULL
                GROUP BY {group}, m.factor_id, m.run_id, r.completed_at
            """, (sub_flag, ic_scope, choice["factor_bar_interval"], choice["universe_key"]))
            filters = {"ic_scope": ic_scope, "interval": choice["factor_bar_interval"],
                       "universe_key": choice["universe_key"]}
            return MetricScopeSnapshot(kind, as_of, filters, tuple(rows))

    def summaries_at(self, sample: SummarySample, as_of: datetime) -> SummarySample:
        """Read each factor's latest completed row at a supplied aware PIT boundary.

        Retains the existing exact partition and does not backfill null metrics from
        older runs. Naive dates raise ValueError; sanitized database errors propagate.
        Formula evidence is intentionally not fetched for research-search checks.
        """
        if as_of.tzinfo is None:
            raise ValueError("summary PIT boundary must be timezone-aware")
        predicate = " AND ".join("m." + key + " <=> %s" for key in SUMMARY_KEYS)
        cutoff = as_of.astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
        with self._snapshot() as tx:
            rows = tx.fetch_all(f"""SELECT ranked.* FROM (
                SELECT m.*, r.completed_at AS run_completed_at,
                    ROW_NUMBER() OVER (PARTITION BY m.factor_id ORDER BY r.completed_at DESC,m.updated_at DESC,m.id DESC) AS rn
                FROM factor_ic_summary_metrics m JOIN factor_ic_runs r ON r.run_id=m.run_id AND r.status='completed'
                WHERE m.is_sub_factor_id=%s AND {predicate} AND r.completed_at<=%s
                ) ranked WHERE rn=1 ORDER BY factor_id""",
                (_TABLES[sample.kind][2], *(sample.scope[key] for key in SUMMARY_KEYS), cutoff))
            return SummarySample(sample.kind, as_of, dict(sample.scope), tuple(rows), ())

    def rank_theme_memberships(self, sample: SummarySample) -> dict[str, frozenset[int]]:
        """Read parent-theme membership for the existing small rank partition.

        Returns a theme-to-factor-ID mapping, with empty set entries for other known
        themes. No threshold or rank logic is evaluated here; DB errors propagate.
        """
        if not sample.rows:
            return {}
        ids = tuple(row["factor_id"] for row in sample.rows)
        placeholders = ",".join(["%s"] * len(ids))
        with self._snapshot() as tx:
            themes = tx.fetch_all("SELECT theme_key FROM themes ORDER BY theme_key")
            if sample.kind == "sub_factor":
                rows = tx.fetch_all(f"""SELECT DISTINCT t.theme_key, rel.sub_factor_id AS factor_id
                    FROM factor_sub_factor_relations rel JOIN factor_theme_relations ftr ON ftr.factor_id=rel.factor_id
                    JOIN themes t ON t.id=ftr.theme_id WHERE rel.sub_factor_id IN ({placeholders})""", ids)
            else:
                rows = tx.fetch_all(f"""SELECT DISTINCT t.theme_key, ftr.factor_id FROM factor_theme_relations ftr
                    JOIN themes t ON t.id=ftr.theme_id WHERE ftr.factor_id IN ({placeholders})""", ids)
        return {theme["theme_key"]: frozenset(row["factor_id"] for row in rows if row["theme_key"] == theme["theme_key"])
                for theme in themes}

    def research_validity_counts(self, sample: SummarySample) -> tuple[dict[str, int], int]:
        """Count explicit validity over the interval catalog LEFT JOIN latest scoped summaries.

        Returns status counts plus the number of ambiguous multi-validity matches.
        Missing summary/validity maps to unknown, matching the research statistics
        contract. Only DB aggregates are returned, not an unbounded catalog dump.
        Invalid scope raises ValueError; sanitized read-only DB failures propagate.
        """
        scope = sample.scope["ic_scope"]
        if scope not in {"time_series", "cross_sectional"}:
            raise ValueError("unsupported research validity scope")
        flag, table = _TABLES[sample.kind][2], _TABLES[sample.kind][0]
        where = " AND ".join(f"m.{key} <=> %s" for key in SUMMARY_KEYS)
        cutoff = sample.as_of.astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
        with self._snapshot() as tx:
            rows = tx.fetch_all(f"""WITH ranked AS (
                SELECT m.id AS summary_id,m.factor_id,m.run_id,
                    ROW_NUMBER() OVER (PARTITION BY m.factor_id ORDER BY r.completed_at DESC,m.updated_at DESC,m.id DESC) AS rn
                FROM factor_ic_summary_metrics m JOIN factor_ic_runs r ON r.run_id=m.run_id
                WHERE m.is_sub_factor_id=%s AND {where} AND r.status='completed' AND r.completed_at<=%s
                ), per_factor AS (
                SELECT catalog.id,COUNT(v.id) AS match_count,COUNT(DISTINCT v.{scope}_status) AS status_count,
                    CASE WHEN COUNT(v.id)=0 OR MAX(v.{scope}_status) IS NULL
                              OR LOWER(MAX(v.{scope}_status)) NOT IN ('valid','invalid','unknown')
                         THEN 'unknown' ELSE LOWER(MAX(v.{scope}_status)) END AS validity_status
                FROM {table} catalog LEFT JOIN ranked latest ON latest.factor_id=catalog.id AND latest.rn=1
                LEFT JOIN factor_validity_status v ON v.is_sub_factor_id=%s AND v.factor_id=catalog.id
                    AND v.run_id=latest.run_id AND v.{scope}_summary_id=latest.summary_id
                WHERE catalog.factor_bar_interval=%s GROUP BY catalog.id
                ) SELECT validity_status,COUNT(*) AS factor_count,
                    SUM(match_count>1 OR status_count>1) AS ambiguous_count
                    FROM per_factor GROUP BY validity_status""",
                (flag, *(sample.scope[key] for key in SUMMARY_KEYS), cutoff, flag, sample.scope["factor_bar_interval"]))
        return ({status: sum(int(row["factor_count"]) for row in rows if row["validity_status"] == status)
                 for status in ("valid", "invalid", "unknown")},
                sum(int(row.get("ambiguous_count") or 0) for row in rows))

    def research_catalog_snapshot(self, sample: SummarySample) -> ResearchCatalogSnapshot:
        """Read typed catalog members with latest exact-scope metrics and linked validity.

        Missing summaries and validity remain explicit unknown members. Only slim
        final result columns are read, not formula inputs or OOS arrays. Multiple
        matching validity rows are retained and counted as oracle ambiguity instead
        of picking an arbitrary status. Invalid scope/kind raises ValueError;
        sanitized read-only database failures propagate.
        """
        scope = sample.scope["ic_scope"]
        if sample.kind not in _TABLES or scope not in {"time_series", "cross_sectional"}:
            raise ValueError("unsupported research catalog scope or kind")
        table, name, flag = _TABLES[sample.kind]
        where = " AND ".join(f"m.{key} <=> %s" for key in SUMMARY_KEYS)
        cutoff = sample.as_of.astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
        with self._snapshot() as tx:
            rows = tx.fetch_all(f"""WITH ranked AS (
                SELECT m.id AS metric_id,m.factor_id,m.run_id,m.factor_bar_interval,m.scoring_version,
                    m.coverage_mean,m.icir,m.rank_icir,m.oos_icir,m.rank_oos_icir,m.final_score,m.valid_slice_count,
                    ROW_NUMBER() OVER (PARTITION BY m.factor_id ORDER BY r.completed_at DESC,m.updated_at DESC,m.id DESC) AS rn
                FROM factor_ic_summary_metrics m JOIN factor_ic_runs r ON r.run_id=m.run_id
                WHERE m.is_sub_factor_id=%s AND {where} AND r.status='completed' AND r.completed_at<=%s
                ) SELECT catalog.id AS factor_id,catalog.{name} AS name,catalog.cn_name,
                    catalog.factor_bar_interval,latest.metric_id,latest.run_id,latest.scoring_version,
                    latest.coverage_mean,latest.icir,latest.rank_icir,latest.oos_icir,latest.rank_oos_icir,
                    latest.final_score,latest.valid_slice_count,v.id AS validity_id,v.run_id AS validity_run_id,
                    v.time_series_status,v.cross_sectional_status,v.overall_status,
                    CASE WHEN v.{scope}_status IS NULL OR LOWER(v.{scope}_status) NOT IN ('valid','invalid','unknown')
                         THEN 'unknown' ELSE LOWER(v.{scope}_status) END AS validity_status
                FROM {table} catalog LEFT JOIN ranked latest ON latest.factor_id=catalog.id AND latest.rn=1
                LEFT JOIN factor_validity_status v ON v.is_sub_factor_id=%s AND v.factor_id=catalog.id
                    AND v.run_id=latest.run_id AND v.{scope}_summary_id=latest.metric_id
                WHERE catalog.factor_bar_interval=%s ORDER BY catalog.id,v.id""",
                (flag, *(sample.scope[key] for key in SUMMARY_KEYS), cutoff, flag, sample.scope["factor_bar_interval"]))
        identities = [row["factor_id"] for row in rows]
        return ResearchCatalogSnapshot(sample, tuple(rows), len(identities) - len(set(identities)))

    def one_dimension_research_sample(self, shape: str) -> tuple[ValiditySample, str] | None:
        """Discover a latest CS metric whose overall validity is TS-only or CS-only.

        Returns its complete validity evidence and actual catalog name, or None when
        no eligible natural candidate exists in the bounded discovery set. Invalid
        shape raises ValueError and read-only DB errors propagate.
        """
        if shape not in {"ts_only", "cs_only"}:
            raise ValueError("one-dimension search requires ts_only or cs_only")
        ts, cs = (1, 0) if shape == "ts_only" else (0, 1)
        with self._snapshot() as tx:
            as_of = self._clock(tx).astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
            rows = tx.fetch_all("""SELECT v.* FROM factor_validity_status v
                JOIN factor_ic_runs r ON r.run_id=v.run_id AND r.status='completed'
                WHERE v.is_sub_factor_id=1 AND v.overall_is_valid=1 AND v.time_series_is_valid=%s
                  AND v.cross_sectional_is_valid=%s AND v.time_series_summary_id IS NOT NULL
                  AND v.cross_sectional_summary_id IS NOT NULL AND r.completed_at<=%s
                ORDER BY v.updated_at DESC,v.id DESC LIMIT 30""", (ts, cs, as_of))
            for row in rows:
                sample = self._validity_entities(tx, row)
                summary = sample.summaries["cs"]
                if not summary or summary.get("factor_id") != row["factor_id"] or summary.get("run_id") != row["run_id"]:
                    continue
                predicate = " AND ".join(f"m.{key} <=> %s" for key in SUMMARY_KEYS)
                latest = tx.fetch_one(f"""SELECT m.id FROM factor_ic_summary_metrics m
                    JOIN factor_ic_runs r ON r.run_id=m.run_id AND r.status='completed'
                    WHERE m.is_sub_factor_id=1 AND m.factor_id=%s AND {predicate} AND r.completed_at<=%s
                    ORDER BY r.completed_at DESC,m.updated_at DESC,m.id DESC LIMIT 1""",
                    (row["factor_id"], *(summary[key] for key in SUMMARY_KEYS), as_of))
                catalog = tx.fetch_one("SELECT sub_factor_name AS name FROM sub_factors WHERE id=%s", (row["factor_id"],))
                if latest and latest["id"] == summary["id"] and catalog and catalog.get("name"):
                    return sample, catalog["name"]
        return None

    def validity_sample(self, shape: str = "any", kind: Kind = "sub_factor") -> ValiditySample | None:
        """Discover a historical shape with same-run TS/CS evidence, not a latest-row oracle.

        Callers verifying this row must supply its Run explicitly. Default-Run
        callers must independently read validity_candidates at their fixed as_of.
        Unknown shape/kind raises ValueError; database errors remain sanitized.
        """
        if shape not in {"any", "ts_only", "cs_only", "both"} or kind not in _TABLES:
            raise ValueError("unsupported validity shape or kind")
        flag = _TABLES[kind][2]
        clauses = ["v.is_sub_factor_id=%s", "v.time_series_summary_id IS NOT NULL", "v.cross_sectional_summary_id IS NOT NULL",
                   "v.overall_is_valid=1", "r.status='completed'", "ts.id=v.time_series_summary_id", "cs.id=v.cross_sectional_summary_id",
                   "ts.run_id=v.run_id", "cs.run_id=v.run_id", "ts.ic_scope='time_series'", "cs.ic_scope='cross_sectional'"]
        if shape == "ts_only": clauses.append("v.time_series_is_valid=1 AND v.cross_sectional_is_valid=0")
        elif shape == "cs_only": clauses.append("v.time_series_is_valid=0 AND v.cross_sectional_is_valid=1")
        elif shape == "both": clauses.append("v.time_series_is_valid=1 AND v.cross_sectional_is_valid=1")
        with self._snapshot() as tx:
            row = tx.fetch_one(f"""SELECT v.*, r.completed_at AS run_completed_at FROM factor_validity_status v
                JOIN factor_ic_runs r ON r.run_id=v.run_id
                JOIN factor_ic_summary_metrics ts ON ts.id=v.time_series_summary_id
                JOIN factor_ic_summary_metrics cs ON cs.id=v.cross_sectional_summary_id
                WHERE {' AND '.join(clauses)} ORDER BY v.updated_at DESC, v.id DESC LIMIT 1""", (flag,))
            if row is None: return None
            summaries = {}
            for name, sid in (("ts", row.get("time_series_summary_id")), ("cs", row.get("cross_sectional_summary_id"))):
                summaries[name] = tx.fetch_one("SELECT * FROM factor_ic_summary_metrics WHERE id=%s", (sid,)) or {}
            return ValiditySample(row, summaries)

    def validity_candidates(
        self, sample: ValiditySample, scope: str, as_of: datetime, *, include_unavailable: bool = False,
    ) -> tuple[ValiditySample, ...]:
        """Read every visible validity candidate for the selected factor and scope.

        No valid-flag/shape filter, newest-row limit, or summary inner join may
        hide a newer invalid/incomplete row. The requested mode/symbol are resolved
        in Service from the returned FK entities. Invalid scope/naive as_of raises
        ValueError; missing rows return (), and database errors are sanitized.
        include_unavailable keeps later versions for explicit-Run history/first
        publication proof; it never implies those versions were visible at as_of.
        """
        if scope not in {"ts", "cs"} or as_of.tzinfo is None:
            raise ValueError("validity candidates require ts/cs scope and aware as_of")
        keys = ("factor_id", "is_sub_factor_id", "universe_key", "factor_bar_interval",
                "factor_window_bars", "return_bar_interval", "forward_return_bars", "window_scope")
        prefix = "time_series" if scope == "ts" else "cross_sectional"
        version = f"{prefix}_scoring_version"
        where = " AND ".join(f"v.{key} <=> %s" for key in (*keys, version))
        cutoff = as_of.astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
        visibility = "" if include_unavailable else " AND r.completed_at<=%s AND v.updated_at<=%s"
        parameters = (*(sample.validity.get(key) for key in (*keys, version)),
                      *((cutoff, cutoff) if not include_unavailable else ()))
        with self._snapshot() as tx:
            rows = tx.fetch_all(f"""SELECT v.*,r.completed_at AS run_completed_at
                FROM factor_validity_status v
                JOIN factor_ic_runs r ON r.run_id=v.run_id AND r.status='completed'
                WHERE {where}{visibility}
                ORDER BY v.id""", parameters)
            return tuple(self._validity_entities(tx, row) for row in rows)

    def validity_history(self, kind: Kind = "sub_factor") -> tuple[ValiditySample, ...]:
        """Return distinct completed runs from one exact validity partition.

        Returns an empty tuple when no natural multi-run partition exists. Invalid kinds
        raise ValueError; database failures are sanitized by the read-only snapshot.
        """
        if kind not in _TABLES:
            raise ValueError("unsupported validity kind")
        flag = _TABLES[kind][2]
        keys = ("factor_id", "is_sub_factor_id", "universe_key", "factor_bar_interval",
                "factor_window_bars", "return_bar_interval", "forward_return_bars", "window_scope",
                "time_series_scoring_version", "cross_sectional_scoring_version")
        partition = ", ".join("v." + key for key in keys)
        joins = """FROM factor_validity_status v
            JOIN factor_ic_runs r ON r.run_id=v.run_id AND r.status='completed'
            JOIN factor_ic_summary_metrics ts ON ts.id=v.time_series_summary_id
            JOIN factor_ic_summary_metrics cs ON cs.id=v.cross_sectional_summary_id"""
        predicates = ["v.is_sub_factor_id=%s", "ts.ic_scope='time_series'", "cs.ic_scope='cross_sectional'",
                      "COALESCE(ts.symbol,'')=''", "COALESCE(cs.symbol,'')=''",
                      "ts.calculation_mode='direct'", "cs.calculation_mode='direct'"]
        for alias in ("ts", "cs"):
            predicates.extend(f"{alias}.{key} <=> v.{key}" for key in keys[:8])
            predicates.append(f"{alias}.run_id=v.run_id")
            validity_version = "time_series" if alias == "ts" else "cross_sectional"
            predicates.append(f"{alias}.scoring_version <=> v.{validity_version}_scoring_version")
        with self._snapshot() as tx:
            cutoff = self._clock(tx).astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
            group = tx.fetch_one(f"""SELECT {partition} {joins}
                WHERE {' AND '.join(predicates)} AND r.completed_at<=%s GROUP BY {partition}
                HAVING COUNT(DISTINCT v.run_id)>=2
                ORDER BY MAX(r.completed_at) DESC, MAX(v.id) DESC LIMIT 1""", (flag, cutoff))
            if group is None:
                return ()
            exact = " AND ".join(f"v.{key} <=> %s" for key in keys)
            rows = tx.fetch_all(f"""SELECT ranked.* FROM (
                SELECT v.*, r.completed_at AS run_completed_at, ROW_NUMBER() OVER (PARTITION BY v.run_id ORDER BY v.updated_at DESC, v.id DESC) AS run_row
                {joins} WHERE {' AND '.join(predicates)} AND {exact} AND r.completed_at<=%s
                ) ranked WHERE run_row=1 ORDER BY run_completed_at DESC, id DESC""",
                (flag, *(group[key] for key in keys), cutoff))
            result=[]
            for row in rows:
                summaries={"ts": tx.fetch_one("SELECT * FROM factor_ic_summary_metrics WHERE id=%s", (row.get("time_series_summary_id"),)) or {}, "cs": tx.fetch_one("SELECT * FROM factor_ic_summary_metrics WHERE id=%s", (row.get("cross_sectional_summary_id"),)) or {}}
                result.append(ValiditySample(row, summaries))
            return tuple(result)

    def incomplete_route_validity(self) -> tuple[ValiditySample, ...]:
        """Read active-route factors whose newest validity claims unsupported evidence.

        Returns an empty tuple when there are no natural incomplete rows. This does
        not declare historical incomplete DB rows a product failure; the Service checks
        whether MCP exposes them as valid. Read-only database errors propagate.
        """
        with self._snapshot() as tx:
            rows = tx.fetch_all("""SELECT latest.* FROM (
                SELECT v.*, r.completed_at AS run_completed_at,
                       ROW_NUMBER() OVER (PARTITION BY v.factor_id ORDER BY v.updated_at DESC,v.id DESC) AS latest_row
                FROM factor_validity_status v LEFT JOIN factor_ic_runs r ON r.run_id=v.run_id
                WHERE v.is_sub_factor_id=1 AND EXISTS (
                    SELECT 1 FROM market_environment_factor_route route
                    WHERE route.factor_id=v.factor_id AND route.factor_type='sub_factor' AND route.is_active=1)
                ) latest WHERE latest_row=1 AND (time_series_is_valid=1 OR cross_sectional_is_valid=1) AND (
                    time_series_summary_id IS NULL OR cross_sectional_summary_id IS NULL OR NOT EXISTS (
                        SELECT 1 FROM factor_ic_summary_metrics s WHERE s.id=latest.time_series_summary_id)
                    OR NOT EXISTS (SELECT 1 FROM factor_ic_summary_metrics s WHERE s.id=latest.cross_sectional_summary_id))
                ORDER BY updated_at DESC,id DESC""")
            return tuple(self._validity_entities(tx, row) for row in rows)

    def validity_by_id(self, identifier: int) -> ValiditySample | None:
        """Read a returned validity ID and both FK entities; absent ID returns None.

        Invalid IDs raise ValueError, and sanitized database failures propagate.
        No validity judgment or API call is performed in the repository.
        """
        if isinstance(identifier, bool) or not isinstance(identifier, int) or identifier <= 0:
            raise ValueError("validity identifier must be a positive integer")
        with self._snapshot() as tx:
            row = tx.fetch_one("SELECT * FROM factor_validity_status WHERE id=%s", (identifier,))
            return self._validity_entities(tx, row) if row else None

    @staticmethod
    def _validity_entities(tx: DatabaseTransaction, row: dict[str, Any]) -> ValiditySample:
        summaries = {scope: tx.fetch_one("SELECT * FROM factor_ic_summary_metrics WHERE id=%s", (row.get(field),)) or {}
                     for scope, field in (("ts", "time_series_summary_id"), ("cs", "cross_sectional_summary_id"))}
        return ValiditySample(row, summaries)

    def absent_factor_ref(self, kind: Kind = "sub_factor") -> str:
        """Return a syntactically valid ref verified absent in a read-only test snapshot.

        The candidate is above MAX(id), never a hard-coded business entity. Invalid
        kinds raise ValueError; database access failures propagate sanitized.
        """
        if kind not in _TABLES:
            raise ValueError("unsupported factor kind")
        table = _TABLES[kind][0]
        with self._snapshot() as tx:
            row = tx.fetch_one(f"SELECT COALESCE(MAX(id),0)+1000000 AS missing_id FROM {table}")
            if row is None:
                raise ValueError("missing factor identity could not be discovered")
            return f"{kind}:{int(row['missing_id'])}"

    def validity_peers(self, sample: ValiditySample) -> tuple[ValiditySample, ...]:
        """Read two distinct latest factor validity rows sharing the exact batch-query scope.

        A pair may span runs because an omitted-run batch resolves each factor
        independently. Missing natural peers returns fewer than two; DB errors propagate.
        """
        keys = ("universe_key", "factor_bar_interval", "factor_window_bars", "return_bar_interval",
                "forward_return_bars", "window_scope", "time_series_scoring_version", "cross_sectional_scoring_version")
        where = " AND ".join(f"v.{key} <=> %s" for key in keys)
        with self._snapshot() as tx:
            cutoff = self._clock(tx).astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
            rows = tx.fetch_all(f"""SELECT chosen.* FROM (
                SELECT v.*,ROW_NUMBER() OVER (PARTITION BY v.factor_id ORDER BY v.updated_at DESC,v.id DESC) AS rn
                FROM factor_validity_status v JOIN factor_ic_runs r ON r.run_id=v.run_id AND r.status='completed'
                JOIN factor_ic_summary_metrics ts ON ts.id=v.time_series_summary_id AND ts.run_id=v.run_id AND ts.factor_id=v.factor_id
                JOIN factor_ic_summary_metrics cs ON cs.id=v.cross_sectional_summary_id AND cs.run_id=v.run_id AND cs.factor_id=v.factor_id
                WHERE v.is_sub_factor_id=1 AND {where} AND r.completed_at<=%s
                  AND ts.ic_scope='time_series' AND cs.ic_scope='cross_sectional'
                  AND ts.calculation_mode=%s AND cs.calculation_mode=%s
                  AND COALESCE(ts.symbol,'')=COALESCE(%s,'') AND COALESCE(cs.symbol,'')=COALESCE(%s,'')
                ) chosen WHERE rn=1 ORDER BY factor_id LIMIT 2""",
                (*(sample.validity[key] for key in keys), cutoff,
                 sample.summaries["ts"]["calculation_mode"], sample.summaries["cs"]["calculation_mode"],
                 sample.summaries["ts"].get("symbol"), sample.summaries["cs"].get("symbol")))
            return tuple(self._validity_entities(tx, row) for row in rows)

    def summaries_for_validity_scope(self, sample: ValiditySample, scope: str) -> tuple[dict[str, Any], ...]:
        """Find all independent same-run metric periods even when the validity FK is missing.

        No matching rows returns an empty tuple. Invalid scopes raise ValueError and
        DB failures propagate. The endpoint exposes an ic_summaries array; multiple
        persisted periods in one Run must not be silently reduced to one latest row.
        """
        if scope not in {"ts", "cs"}:
            raise ValueError("scope must be ts or cs")
        name = "time_series" if scope == "ts" else "cross_sectional"
        keys = ("run_id", "factor_id", "is_sub_factor_id", "universe_key", "factor_bar_interval",
                "factor_window_bars", "return_bar_interval", "forward_return_bars", "window_scope")
        where = " AND ".join(f"m.{key} <=> %s" for key in keys)
        with self._snapshot() as tx:
            rows = tx.fetch_all(f"""SELECT m.* FROM factor_ic_summary_metrics m
                JOIN factor_ic_runs r ON r.run_id=m.run_id AND r.status='completed'
                WHERE {where} AND m.ic_scope=%s AND m.calculation_mode='direct'
                  AND COALESCE(m.symbol,'')='' AND m.scoring_version <=> %s
                ORDER BY m.period_start,m.period_end,m.id""",
                (*(sample.validity[key] for key in keys), name, sample.validity.get(f"{name}_scoring_version")))
            return tuple(rows)

    def slice_sample(self, scope: str = "time_series", kind: Kind = "sub_factor", *, symbol_mode: str = "any", min_rows: int = 1) -> SliceSample | None:
        """Discover an exact persisted slice scope, optionally symbol or aggregate only.

        Returns None without natural data; invalid selectors raise ValueError and DB
        failures propagate sanitized. Symbol identity is preserved in both queries.
        """
        if scope not in {"time_series", "cross_sectional"} or kind not in _TABLES or symbol_mode not in {"any", "symbol", "aggregate"} or isinstance(min_rows, bool) or not isinstance(min_rows, int) or not 1 <= min_rows <= 100:
            raise ValueError("unsupported slice scope or kind")
        symbol_filter = {"any": "1=1", "symbol": "COALESCE(m.symbol,'')<>''",
                         "aggregate": "COALESCE(m.symbol,'')=''"}[symbol_mode]
        with self._snapshot() as tx:
            summary = tx.fetch_one(f"""SELECT m.* FROM factor_ic_summary_metrics m
                JOIN factor_ic_runs r ON r.run_id=m.run_id AND r.status='completed'
                WHERE m.is_sub_factor_id=%s AND m.ic_scope=%s AND {symbol_filter} AND m.slice_count >= %s
                  AND (%s=1 OR m.window_scope='rolling')
                  AND EXISTS (SELECT 1 FROM factor_ic_slice_metrics s WHERE s.run_id=m.run_id AND s.factor_id=m.factor_id AND s.is_sub_factor_id=m.is_sub_factor_id AND s.ic_scope=m.ic_scope AND s.calculation_mode=m.calculation_mode AND s.factor_bar_interval=m.factor_bar_interval AND s.factor_window_bars=m.factor_window_bars AND s.return_bar_interval=m.return_bar_interval AND s.forward_return_bars=m.forward_return_bars AND s.universe_key=m.universe_key AND s.window_scope=m.window_scope AND COALESCE(s.symbol,'')=COALESCE(m.symbol,''))
                ORDER BY m.updated_at DESC, m.id DESC LIMIT 1""", (_TABLES[kind][2], scope, min_rows, min_rows))
            if summary is None: return None
            rows = tx.fetch_all("""SELECT * FROM factor_ic_slice_metrics WHERE run_id=%s AND factor_id=%s AND is_sub_factor_id=%s AND ic_scope=%s AND calculation_mode=%s AND factor_bar_interval=%s AND factor_window_bars=%s AND return_bar_interval=%s AND forward_return_bars=%s AND universe_key=%s AND window_scope=%s AND COALESCE(symbol,'')=COALESCE(%s,'') ORDER BY as_of_time, id""", tuple(summary.get(k) for k in ("run_id","factor_id","is_sub_factor_id","ic_scope","calculation_mode","factor_bar_interval","factor_window_bars","return_bar_interval","forward_return_bars","universe_key","window_scope","symbol")))
            return SliceSample(summary, tuple(rows))

    def slices_for_summary(self, summary: dict[str, Any], *, discover_symbol: bool = False) -> SliceSample | None:
        """Read exact-run slices for a persisted summary, optionally select a real symbol.

        Aggregate TS summaries can be backed only by symbol-specific slices. When
        discover_symbol is true one naturally persisted symbol is selected; no metric
        value is generated. No rows returns None; malformed input/DB errors propagate.
        """
        keys = ("run_id", "factor_id", "is_sub_factor_id", "ic_scope", "calculation_mode",
                "factor_bar_interval", "factor_window_bars", "return_bar_interval", "forward_return_bars",
                "universe_key", "window_scope")
        where = " AND ".join(f"{key} <=> %s" for key in keys)
        parameters = tuple(summary[key] for key in keys)
        with self._snapshot() as tx:
            selected = dict(summary)
            if discover_symbol:
                profile = tx.fetch_one(f"""SELECT COALESCE(symbol,'') AS symbol
                    FROM factor_ic_slice_metrics WHERE {where}
                    GROUP BY COALESCE(symbol,'') ORDER BY (COALESCE(symbol,'')<>'' ) DESC, symbol LIMIT 1""", parameters)
                if profile is None:
                    return None
                selected["symbol"] = profile["symbol"]
            rows = tx.fetch_all(f"""SELECT * FROM factor_ic_slice_metrics WHERE {where}
                AND COALESCE(symbol,'')=COALESCE(%s,'') ORDER BY as_of_time, id""",
                (*parameters, selected.get("symbol")))
            return SliceSample(selected, tuple(rows)) if rows else None

    def related_slice_samples(self) -> dict[str, SliceSample]:
        """Discover TS symbol/aggregate and CS aggregate in one factor/run partition.

        Missing natural shapes are omitted, allowing each Case to report its own data
        precondition. Read-only DB errors propagate; no source report IDs are used.
        """
        base = self.slice_sample("time_series", symbol_mode="symbol", min_rows=8)
        if base is None:
            return {}
        keys = ("run_id", "factor_id", "is_sub_factor_id", "calculation_mode", "factor_bar_interval",
                "factor_window_bars", "return_bar_interval", "forward_return_bars", "universe_key",
                "window_scope", "scoring_version")
        where = " AND ".join(f"{key} <=> %s" for key in keys)
        params = tuple(base.summary[key] for key in keys)
        result = {"ts_symbol": base}
        with self._snapshot() as tx:
            summaries = [tx.fetch_one(f"""SELECT * FROM factor_ic_summary_metrics WHERE {where}
                AND ic_scope=%s AND COALESCE(symbol,'')='' ORDER BY updated_at DESC,id DESC LIMIT 1""",
                (*params, scope)) for scope in ("time_series", "cross_sectional")]
        for name, summary in zip(("ts_aggregate", "cs_aggregate"), summaries):
            if summary is not None:
                sample = self.slices_for_summary(summary)
                if sample is not None:
                    result[name] = sample
        return result

    def slice_watermark(self, sample: SliceSample) -> dict[str, Any]:
        """Return a scope-local read watermark; a change proves drift, not an MCP write.

        Counts, IDs and creation timestamps are read only. DB errors propagate; the
        caller must not attribute concurrent shared-environment changes to the API.
        """
        keys = ("run_id", "factor_id", "is_sub_factor_id", "ic_scope", "calculation_mode",
                "factor_bar_interval", "factor_window_bars", "return_bar_interval", "forward_return_bars",
                "universe_key", "window_scope")
        where = " AND ".join(f"{key} <=> %s" for key in keys)
        with self._snapshot() as tx:
            return tx.fetch_one(f"""SELECT COUNT(*) AS row_count, MAX(id) AS max_id,
                MAX(created_at) AS max_created FROM factor_ic_slice_metrics WHERE {where}
                AND COALESCE(symbol,'')=COALESCE(%s,'')""",
                (*(sample.summary[key] for key in keys), sample.summary.get("symbol"))) or {}

    @staticmethod
    def _clock(tx: DatabaseTransaction) -> datetime:
        row = tx.fetch_one("SELECT UTC_TIMESTAMP(6) AS as_of_time")
        if row is None or not isinstance(row.get("as_of_time"), datetime):
            raise ValueError("database clock missing")
        return row["as_of_time"].replace(tzinfo=timezone.utc)
