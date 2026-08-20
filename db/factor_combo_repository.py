"""组合因子台测试所需的 MySQL 数据访问与测试数据状态准备。"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from db.client import DatabaseClient, DatabaseTransaction


@dataclass(frozen=True)
class SubFactorChoice:
    """表示可用于组合因子测试的一个真实子因子。

    参数包含子因子主键、唯一名称及一个可用的母因子主键和名称。
    返回值由 ``FactorComboRepository`` 的因子选择方法产生，供表单和 Worker 组件构造使用。
    """

    sub_factor_id: int
    sub_factor_name: str
    parent_factor_id: int
    parent_factor_name: str


@dataclass(frozen=True)
class ParentFactorChoice:
    """表示至少关联多个子因子的真实母因子。

    参数包含母因子主键、唯一名称以及已排序的关联子因子。
    返回值由 ``find_parent_with_sub_factors`` 产生，供验证母因子展开规则使用。
    """

    factor_id: int
    factor_name: str
    sub_factors: tuple[SubFactorChoice, ...]


class FactorComboRepository:
    """封装组合因子测试的 MySQL 查询、受控状态准备和数据清理。"""

    def __init__(self, client: DatabaseClient, environment: str) -> None:
        """初始化组合因子数据仓储。

        参数 ``client`` 是已配置 MySQL 的 ``DatabaseClient``，``environment`` 是当前自动化环境名称。
        不返回值；所有写操作在执行前都会校验环境必须为 ``test``。
        """

        self._client = client
        self._environment = environment.strip().lower()

    def find_parent_with_sub_factors(self, minimum_sub_factors: int = 2) -> ParentFactorChoice | None:
        """查找一个拥有足够关联子因子的真实母因子。

        参数 ``minimum_sub_factors`` 是所需最少关联子因子数。
        返回 ``ParentFactorChoice``；测试库中不存在符合条件的母因子时返回 ``None``。
        """

        rows = self._client.fetch_all(
            """
            SELECT
                f.id AS factor_id,
                f.factor_name,
                r.sub_factor_id,
                sf.sub_factor_name
            FROM factors AS f
            INNER JOIN factor_sub_factor_relations AS r
                ON r.factor_id = f.id
            INNER JOIN sub_factors AS sf
                ON sf.id = r.sub_factor_id
            WHERE f.factor_name IS NOT NULL
              AND TRIM(f.factor_name) <> ''
              AND sf.sub_factor_name IS NOT NULL
              AND TRIM(sf.sub_factor_name) <> ''
            ORDER BY f.id ASC, r.sub_factor_id ASC
            """
        )
        grouped: dict[int, list[SubFactorChoice]] = {}
        factor_names: dict[int, str] = {}
        for row in rows:
            factor_id = int(row["factor_id"])
            factor_names[factor_id] = str(row["factor_name"])
            grouped.setdefault(factor_id, []).append(
                SubFactorChoice(
                    sub_factor_id=int(row["sub_factor_id"]),
                    sub_factor_name=str(row["sub_factor_name"]),
                    parent_factor_id=factor_id,
                    parent_factor_name=str(row["factor_name"]),
                )
            )
        for factor_id, sub_factors in grouped.items():
            if len(sub_factors) >= minimum_sub_factors:
                return ParentFactorChoice(
                    factor_id=factor_id,
                    factor_name=factor_names[factor_id],
                    sub_factors=tuple(sub_factors),
                )
        return None

    def find_ranked_parent_with_sub_factors(self, minimum_sub_factors: int = 2) -> ParentFactorChoice | None:
        """查找可按最新时序评分展开的母因子及预期前十二个子因子。

        参数 ``minimum_sub_factors`` 是母因子至少需要具备的有效评分子因子数量。
        返回按时序评分降序、子因子 ID 升序排列的 ``ParentFactorChoice``；没有符合数据时返回 ``None``。
        """

        rows = self._client.fetch_all(
            """
            WITH latest_scored AS (
                SELECT
                    factor_id AS sub_factor_id,
                    time_series_score,
                    ROW_NUMBER() OVER (
                        PARTITION BY factor_id
                        ORDER BY updated_at DESC, id DESC
                    ) AS rn
                FROM factor_validity_status
                WHERE is_sub_factor_id = 1
                  AND universe_key = 'main'
                  AND window_scope = 'rolling'
                  AND time_series_score IS NOT NULL
            )
            SELECT
                f.id AS factor_id,
                f.factor_name,
                relation_item.sub_factor_id,
                sf.sub_factor_name,
                latest_scored.time_series_score
            FROM factors AS f
            INNER JOIN factor_sub_factor_relations AS relation_item
                ON relation_item.factor_id = f.id
            INNER JOIN sub_factors AS sf
                ON sf.id = relation_item.sub_factor_id
            INNER JOIN latest_scored
                ON latest_scored.sub_factor_id = relation_item.sub_factor_id
               AND latest_scored.rn = 1
            WHERE f.factor_name IS NOT NULL
              AND TRIM(f.factor_name) <> ''
              AND sf.sub_factor_name IS NOT NULL
              AND TRIM(sf.sub_factor_name) <> ''
            ORDER BY
                f.id ASC,
                latest_scored.time_series_score DESC,
                relation_item.sub_factor_id ASC
            """
        )
        grouped: dict[int, list[SubFactorChoice]] = {}
        factor_names: dict[int, str] = {}
        for row in rows:
            factor_id = int(row["factor_id"])
            factor_names[factor_id] = str(row["factor_name"])
            grouped.setdefault(factor_id, []).append(
                SubFactorChoice(
                    sub_factor_id=int(row["sub_factor_id"]),
                    sub_factor_name=str(row["sub_factor_name"]),
                    parent_factor_id=factor_id,
                    parent_factor_name=str(row["factor_name"]),
                )
            )
        for factor_id, sub_factors in grouped.items():
            if len(sub_factors) >= minimum_sub_factors:
                return ParentFactorChoice(
                    factor_id=factor_id,
                    factor_name=factor_names[factor_id],
                    sub_factors=tuple(sub_factors[:12]),
                )
        return None

    def find_sub_factor_pair(self) -> tuple[SubFactorChoice, SubFactorChoice] | None:
        """查找两个可用于表单提交的真实子因子。

        不接收参数。
        返回优先来自不同母因子的两个 ``SubFactorChoice``；测试库中不足两个可用子因子时返回 ``None``。
        """

        rows = self._client.fetch_all(
            """
            SELECT
                r.factor_id AS parent_factor_id,
                f.factor_name AS parent_factor_name,
                r.sub_factor_id,
                sf.sub_factor_name
            FROM factor_sub_factor_relations AS r
            INNER JOIN factors AS f
                ON f.id = r.factor_id
            INNER JOIN sub_factors AS sf
                ON sf.id = r.sub_factor_id
            WHERE f.factor_name IS NOT NULL
              AND TRIM(f.factor_name) <> ''
              AND sf.sub_factor_name IS NOT NULL
              AND TRIM(sf.sub_factor_name) <> ''
            ORDER BY r.factor_id ASC, r.sub_factor_id ASC
            """
        )
        choices: list[SubFactorChoice] = []
        seen_sub_factor_ids: set[int] = set()
        for row in rows:
            sub_factor_id = int(row["sub_factor_id"])
            if sub_factor_id in seen_sub_factor_ids:
                continue
            seen_sub_factor_ids.add(sub_factor_id)
            choices.append(
                SubFactorChoice(
                    sub_factor_id=sub_factor_id,
                    sub_factor_name=str(row["sub_factor_name"]),
                    parent_factor_id=int(row["parent_factor_id"]),
                    parent_factor_name=str(row["parent_factor_name"]),
                )
            )
        for first_index, first_choice in enumerate(choices):
            for second_choice in choices[first_index + 1 :]:
                if second_choice.parent_factor_id != first_choice.parent_factor_id:
                    return first_choice, second_choice
        if len(choices) >= 2:
            return choices[0], choices[1]
        return None

    def find_sub_factor_outside_pool(self, pool_id: int) -> SubFactorChoice | None:
        """查找一个不属于指定锁定池的真实子因子。

        参数 ``pool_id`` 是因子池主键。
        返回一个 ``SubFactorChoice``；没有可用池外子因子时返回 ``None``。
        """

        row = self._client.fetch_one(
            """
            SELECT
                r.factor_id AS parent_factor_id,
                f.factor_name AS parent_factor_name,
                sf.id AS sub_factor_id,
                sf.sub_factor_name
            FROM sub_factors AS sf
            INNER JOIN factor_sub_factor_relations AS r
                ON r.sub_factor_id = sf.id
            INNER JOIN factors AS f
                ON f.id = r.factor_id
            WHERE NOT EXISTS (
                SELECT 1
                FROM factor_combo_pool_member AS member
                WHERE member.pool_id = %s
                  AND member.sub_factor_id = sf.id
            )
            ORDER BY sf.id ASC
            LIMIT 1
            """,
            (pool_id,),
        )
        return self._to_sub_factor_choice(row)

    def find_unrelated_parent_factor(self, sub_factor_id: int, excluded_factor_id: int) -> int | None:
        """查找一个与指定子因子没有父子关联的真实母因子。

        参数 ``sub_factor_id`` 是目标子因子主键，``excluded_factor_id`` 是已知合法父因子主键。
        返回不关联该子因子的母因子主键；没有符合条件的母因子时返回 ``None``。
        """

        row = self._client.fetch_one(
            """
            SELECT f.id AS factor_id
            FROM factors AS f
            WHERE f.id <> %s
              AND NOT EXISTS (
                SELECT 1
                FROM factor_sub_factor_relations AS relation_item
                WHERE relation_item.factor_id = f.id
                  AND relation_item.sub_factor_id = %s
              )
            ORDER BY f.id ASC
            LIMIT 1
            """,
            (excluded_factor_id, sub_factor_id),
        )
        return int(row["factor_id"]) if row is not None else None

    def get_form(self, form_id: int) -> dict[str, Any] | None:
        """读取组合研究表单及其关联指针。

        参数 ``form_id`` 是 ``factor_combo_form`` 主键。
        返回标准化后的表单字典；记录不存在时返回 ``None``。
        """

        row = self._client.fetch_one(
            """
            SELECT
                id,
                session_id,
                status,
                factor_combo_pool_id,
                factor_combo_id,
                factor_combo_experiment_info_id,
                pipeline_run_id,
                idempotency_key,
                form_json
            FROM factor_combo_form
            WHERE id = %s
            """,
            (form_id,),
        )
        return self._normalize_json_columns(row, "form_json")

    def count_forms_for_session(self, session_id: int) -> int:
        """统计指定会话下的组合研究表单数量。

        参数 ``session_id`` 是 Chat Session 主键。
        返回表单记录数量；没有记录时返回零。
        """

        row = self._client.fetch_one(
            "SELECT COUNT(*) AS record_count FROM factor_combo_form WHERE session_id = %s",
            (session_id,),
        )
        return int(row["record_count"]) if row is not None else 0

    def get_pool(self, pool_id: int) -> dict[str, Any] | None:
        """读取一个组合因子池的状态和快照。

        参数 ``pool_id`` 是 ``factor_combo_pool.pool_id``。
        返回标准化后的因子池字典；记录不存在时返回 ``None``。
        """

        row = self._client.fetch_one(
            """
            SELECT
                pool_id,
                factor_combo_form_id,
                status,
                filter_json,
                pool_snapshot_hash
            FROM factor_combo_pool
            WHERE pool_id = %s
            """,
            (pool_id,),
        )
        return self._normalize_json_columns(row, "filter_json")

    def get_pool_members(self, form_id: int) -> list[dict[str, Any]]:
        """读取一个表单锁定池的全部成员及对应母因子。

        参数 ``form_id`` 是组合研究表单主键。
        返回按 ``sort_order`` 排序的成员字典列表；表单没有因子池成员时返回空列表。
        """

        return self._client.fetch_all(
            """
            SELECT
                member.id,
                member.factor_combo_form_id,
                member.pool_id,
                member.sub_factor_id,
                member.factor_detail_id,
                member.sort_order,
                sf.sub_factor_name,
                (
                    SELECT MIN(relation_item.factor_id)
                    FROM factor_sub_factor_relations AS relation_item
                    WHERE relation_item.sub_factor_id = member.sub_factor_id
                ) AS parent_factor_id
            FROM factor_combo_pool_member AS member
            INNER JOIN sub_factors AS sf
                ON sf.id = member.sub_factor_id
            WHERE member.factor_combo_form_id = %s
            ORDER BY member.sort_order ASC, member.id ASC
            """,
            (form_id,),
        )

    def get_combo_version(self, version_id: int) -> dict[str, Any] | None:
        """读取一个具体组合版本。

        参数 ``version_id`` 是 ``factor_combo.id``，不是业务级 ``combo_id``。
        返回组合版本字典；记录不存在时返回 ``None``。
        """

        return self._client.fetch_one(
            """
            SELECT
                id,
                combo_id,
                combo_family_key,
                initial_form_id,
                pool_id,
                generation_method,
                experiment_id,
                combo_version_hash,
                status
            FROM factor_combo
            WHERE id = %s
            """,
            (version_id,),
        )

    def count_versions_for_form(self, form_id: int) -> int:
        """统计指定表单产生的组合版本数量。

        参数 ``form_id`` 是组合研究表单主键。
        返回版本数量；没有记录时返回零。
        """

        row = self._client.fetch_one(
            "SELECT COUNT(*) AS record_count FROM factor_combo WHERE initial_form_id = %s",
            (form_id,),
        )
        return int(row["record_count"]) if row is not None else 0

    def count_components(self, version_id: int) -> int:
        """统计具体组合版本的组件数量。

        参数 ``version_id`` 是具体 ``factor_combo.id``。
        返回组件数量；没有记录时返回零。
        """

        row = self._client.fetch_one(
            "SELECT COUNT(*) AS record_count FROM factor_combo_component WHERE combo_id = %s",
            (version_id,),
        )
        return int(row["record_count"]) if row is not None else 0

    def get_components(self, version_id: int) -> list[dict[str, Any]]:
        """读取一个组合版本的全部组件。

        参数 ``version_id`` 是具体 ``factor_combo.id``。
        返回组件字典列表；未写入组件时返回空列表。
        """

        rows = self._client.fetch_all(
            """
            SELECT
                component.id,
                component.combo_id,
                component.component_factor_id,
                factor_item.factor_name,
                component.component_sub_factor_id,
                sub_factor_item.sub_factor_name,
                component.direction,
                component.transform_json,
                component.weight
            FROM factor_combo_component AS component
            INNER JOIN factors AS factor_item
                ON factor_item.id = component.component_factor_id
            INNER JOIN sub_factors AS sub_factor_item
                ON sub_factor_item.id = component.component_sub_factor_id
            WHERE component.combo_id = %s
            ORDER BY
                component.component_sub_factor_id ASC,
                component.component_factor_id ASC,
                component.id ASC
            """,
            (version_id,),
        )
        return [self._normalize_json_columns(row, "transform_json") or {} for row in rows]

    def get_experiment(self, experiment_info_id: int) -> dict[str, Any] | None:
        """读取一个组合因子实验信息记录。

        参数 ``experiment_info_id`` 是 ``factor_combo_experiment_info.id``。
        返回实验字典；记录不存在时返回 ``None``。
        """

        row = self._client.fetch_one(
            """
            SELECT
                id,
                experiment_id,
                combo_id,
                valid,
                failure_reason,
                evaluation_config_json,
                metrics_json,
                train_config_json,
                artifact_uri,
                artifact_hash,
                composite_factor_score
            FROM factor_combo_experiment_info
            WHERE id = %s
            """,
            (experiment_info_id,),
        )
        return self._normalize_json_columns(row, "evaluation_config_json", "metrics_json", "train_config_json")

    def get_experiment_by_external_id(self, experiment_id: str) -> dict[str, Any] | None:
        """按接口幂等键读取组合实验记录。

        参数 ``experiment_id`` 是实验写入接口路径中的业务标识。
        返回实验字典；不存在时返回 ``None``。
        """

        row = self._client.fetch_one(
            """
            SELECT
                id,
                experiment_id,
                combo_id,
                valid,
                failure_reason,
                artifact_uri,
                artifact_hash
            FROM factor_combo_experiment_info
            WHERE experiment_id = %s
            """,
            (experiment_id,),
        )
        return row

    def count_experiments_by_artifact_uri(self, artifact_uri: str) -> int:
        """统计指定 Artifact URI 关联的实验记录数。

        参数 ``artifact_uri`` 是实验产物 URI。
        返回匹配的实验记录数量；没有记录时返回零。
        """

        row = self._client.fetch_one(
            "SELECT COUNT(*) AS record_count FROM factor_combo_experiment_info WHERE artifact_uri = %s",
            (artifact_uri,),
        )
        return int(row["record_count"]) if row is not None else 0

    def count_experiments_by_artifact_hash(self, artifact_sha256: str) -> int:
        """统计指定 Artifact SHA256 对应的实验记录数。

        参数 ``artifact_sha256`` 是十六进制内容摘要。
        返回匹配的实验记录数量；没有记录时返回零。
        """

        row = self._client.fetch_one(
            "SELECT COUNT(*) AS record_count FROM factor_combo_experiment_info WHERE artifact_hash = %s",
            (artifact_sha256.lower(),),
        )
        return int(row["record_count"]) if row is not None else 0

    def get_feedback(self, feedback_id: int) -> dict[str, Any] | None:
        """读取组合报告反馈及下一轮关联指针。

        参数 ``feedback_id`` 是 ``factor_combo_experiment_feedback`` 主键。
        返回反馈字典；记录不存在时返回 ``None``。
        """

        return self._client.fetch_one(
            """
            SELECT
                id,
                form_id,
                feedback_round,
                source_factor_combo_version_id,
                source_combo_id,
                status,
                claimed_at,
                next_pipeline_run_id,
                next_factor_combo_version_id,
                next_experiment_info_id,
                completed_at
            FROM factor_combo_experiment_feedback
            WHERE id = %s
            """,
            (feedback_id,),
        )

    def count_feedback_for_form(self, form_id: int) -> int:
        """统计一个表单的反馈记录数量。

        参数 ``form_id`` 是组合研究表单主键。
        返回反馈记录数量；没有记录时返回零。
        """

        row = self._client.fetch_one(
            "SELECT COUNT(*) AS record_count FROM factor_combo_experiment_feedback WHERE form_id = %s",
            (form_id,),
        )
        return int(row["record_count"]) if row is not None else 0

    def get_registration(self, combo_id: int) -> dict[str, Any] | None:
        """按业务级组合 ID 读取登记完成标记。

        参数 ``combo_id`` 是 ``factor_combo.combo_id``。
        返回登记字典；组合尚未登记时返回 ``None``。
        """

        return self._client.fetch_one(
            """
            SELECT
                id,
                combo_id,
                combo_version_hash,
                factor_id,
                sub_factor_id
            FROM factor_combo_registered_factor
            WHERE combo_id = %s
            """,
            (combo_id,),
        )

    def get_registered_sub_factor(self, sub_factor_id: int) -> dict[str, Any] | None:
        """读取登记接口创建的复合子因子。

        参数 ``sub_factor_id`` 是登记响应中的子因子主键。
        返回子因子核心字段；记录不存在时返回 ``None``。
        """

        row = self._client.fetch_one(
            """
            SELECT id, serial_number, sub_factor_name, cn_name, type, metadata
            FROM sub_factors
            WHERE id = %s
            """,
            (sub_factor_id,),
        )
        return self._normalize_json_columns(row, "metadata")

    def get_registered_factor_detail(self, factor_detail_id: int) -> dict[str, Any] | None:
        """读取登记接口创建的因子详情。

        参数 ``factor_detail_id`` 是登记响应中的详情主键。
        返回因子详情核心字段；记录不存在时返回 ``None``。
        """

        return self._client.fetch_one(
            """
            SELECT id, factor_id, is_sub_factor_id, serial_number, name, params, status
            FROM factors_details
            WHERE id = %s
            """,
            (factor_detail_id,),
        )

    def get_registered_validity_status(self, validity_status_id: int) -> dict[str, Any] | None:
        """读取登记接口创建的有效性快照。

        参数 ``validity_status_id`` 是登记响应中的有效性记录主键。
        返回有效性和审计字段；记录不存在时返回 ``None``。
        """

        row = self._client.fetch_one(
            """
            SELECT
                id,
                factor_id,
                is_sub_factor_id,
                universe_key,
                factor_bar_interval,
                factor_window_bars,
                return_bar_interval,
                forward_return_bars,
                window_scope,
                period_start,
                period_end,
                time_series_score,
                time_series_status,
                time_series_is_valid,
                cross_sectional_score,
                cross_sectional_status,
                cross_sectional_is_valid,
                overall_score,
                overall_status,
                overall_is_valid,
                status_reason_json
            FROM factor_validity_status
            WHERE id = %s
            """,
            (validity_status_id,),
        )
        return self._normalize_json_columns(row, "status_reason_json")

    def get_factor_refresh_calculation_runs(self, sub_factor_id: int) -> list[dict[str, Any]]:
        """读取指定复合子因子在新版指标汇总表中的计算 Run 证据。

        参数 ``sub_factor_id`` 是登记接口创建的复合子因子主键。
        返回按更新时间倒序聚合的 ``factor_ic_summary_metrics`` 记录；每行包含计算 Run 状态、汇总行数和至少一个
        非空 IC/ICIR/t-stat/有效性评分指标的行数。该方法只读权威新版指标表 ``factor_ic_summary_metrics`` 及其 Run 主表
        ``factor_ic_runs``，不会读取已废弃的 ``factor_mining_symbol_window_metric``，数据库异常直接向调用方抛出。
        """

        return self._client.fetch_all(
            """
            SELECT
                summary.factor_id,
                summary.is_sub_factor_id,
                summary.run_id,
                runs.status AS run_status,
                COUNT(*) AS summary_row_count,
                SUM(
                    CASE
                        WHEN summary.coverage_mean IS NOT NULL
                          OR summary.coverage_min IS NOT NULL
                          OR summary.mean_ic IS NOT NULL
                          OR summary.median_ic IS NOT NULL
                          OR summary.std_ic IS NOT NULL
                          OR summary.icir IS NOT NULL
                          OR summary.mean_abs_ic IS NOT NULL
                          OR summary.positive_ic_rate IS NOT NULL
                          OR summary.mean_rank_ic IS NOT NULL
                          OR summary.median_rank_ic IS NOT NULL
                          OR summary.std_rank_ic IS NOT NULL
                          OR summary.rank_icir IS NOT NULL
                          OR summary.mean_abs_rank_ic IS NOT NULL
                          OR summary.positive_rank_ic_rate IS NOT NULL
                          OR summary.ic_t_stat IS NOT NULL
                          OR summary.rank_ic_t_stat IS NOT NULL
                          OR summary.monotonicity_ratio IS NOT NULL
                          OR summary.mean_long_short_return IS NOT NULL
                          OR summary.long_short_annual_return IS NOT NULL
                          OR summary.long_short_t_stat IS NOT NULL
                          OR summary.is_icir IS NOT NULL
                          OR summary.oos_icir IS NOT NULL
                          OR summary.icir_oos_retention IS NOT NULL
                          OR summary.rank_is_icir IS NOT NULL
                          OR summary.rank_oos_icir IS NOT NULL
                          OR summary.rank_icir_oos_retention IS NOT NULL
                          OR summary.ic_score IS NOT NULL
                          OR summary.rank_ic_score IS NOT NULL
                          OR summary.icir_score IS NOT NULL
                          OR summary.rank_icir_score IS NOT NULL
                          OR summary.t_stat_score IS NOT NULL
                          OR summary.oos_retention_score IS NOT NULL
                          OR summary.monotonicity_score IS NOT NULL
                          OR summary.long_short_score IS NOT NULL
                          OR summary.final_score IS NOT NULL
                          OR summary.mean_stratification IS NOT NULL
                        THEN 1
                        ELSE 0
                    END
                ) AS populated_metric_row_count,
                COUNT(DISTINCT summary.ic_scope) AS ic_scope_count,
                MAX(summary.updated_at) AS latest_updated_at
            FROM factor_ic_summary_metrics AS summary
            LEFT JOIN factor_ic_runs AS runs
                ON runs.run_id = summary.run_id
            WHERE summary.factor_id = %s
              AND summary.is_sub_factor_id = 1
            GROUP BY
                summary.factor_id,
                summary.is_sub_factor_id,
                summary.run_id,
                runs.status
            ORDER BY latest_updated_at DESC, summary.run_id DESC
            """,
            (sub_factor_id,),
        )

    def get_factor_refresh_calculation_metrics(self, sub_factor_id: int) -> list[dict[str, Any]]:
        """读取指定复合子因子的新版 IC 汇总明细及其计算 Run 状态。

        参数 ``sub_factor_id`` 是登记接口创建的复合子因子主键。
        返回 ``factor_ic_summary_metrics`` 的明细行，包含完整汇总字段、``summary_id`` 别名和关联的
        ``factor_ic_runs.status``；调用方可据此按有效性快照引用的 Run/summary 精确筛选本次刷新结果。
        方法只读新版指标表和 Run 主表，不读取已废弃的 ``factor_mining_symbol_window_metric``。
        """

        return self._client.fetch_all(
            """
            SELECT
                summary.*,
                summary.id AS summary_id,
                runs.status AS run_status
            FROM factor_ic_summary_metrics AS summary
            LEFT JOIN factor_ic_runs AS runs
                ON runs.run_id = summary.run_id
            WHERE summary.factor_id = %s
              AND summary.is_sub_factor_id = 1
            ORDER BY summary.updated_at DESC, summary.id DESC
            """,
            (sub_factor_id,),
        )

    def get_factor_refresh_validity_snapshots(
        self,
        sub_factor_id: int,
        registration_validity_status_id: int,
    ) -> list[dict[str, Any]]:
        """读取登记初始快照之外、且引用新版汇总结果的有效性快照。

        参数 ``sub_factor_id`` 是登记生成的复合子因子主键，``registration_validity_status_id`` 是登记响应中的初始
        ``factor_validity_status.id``。返回关联的有效性快照及其时序/截面汇总记录身份；查询排除登记初始快照和
        没有任何 summary 外键的登记占位状态；如果后端在原地更新登记快照并补上 summary 外键，该行会被保留。方法只读
        数据库，SQL 或连接异常直接向调用方抛出。
        """

        return self._client.fetch_all(
            """
            SELECT
                validity.id,
                validity.factor_id,
                validity.is_sub_factor_id,
                CASE WHEN validity.id = %s THEN 1 ELSE 0 END AS is_registration_snapshot,
                validity.run_id,
                validity.universe_key,
                validity.factor_bar_interval,
                validity.factor_window_bars,
                validity.return_bar_interval,
                validity.forward_return_bars,
                validity.window_scope,
                validity.period_start,
                validity.period_end,
                validity.time_series_summary_id,
                validity.cross_sectional_summary_id,
                time_summary.run_id AS time_series_summary_run_id,
                time_summary.factor_id AS time_series_summary_factor_id,
                time_summary.is_sub_factor_id AS time_series_summary_is_sub_factor_id,
                cross_summary.run_id AS cross_sectional_summary_run_id,
                cross_summary.factor_id AS cross_sectional_summary_factor_id,
                cross_summary.is_sub_factor_id AS cross_sectional_summary_is_sub_factor_id,
                validity.overall_score,
                validity.overall_status,
                validity.overall_is_valid,
                validity.time_series_score,
                validity.time_series_status,
                validity.time_series_is_valid,
                validity.cross_sectional_score,
                validity.cross_sectional_status,
                validity.cross_sectional_is_valid,
                validity.updated_at
            FROM factor_validity_status AS validity
            LEFT JOIN factor_ic_summary_metrics AS time_summary
                ON time_summary.id = validity.time_series_summary_id
            LEFT JOIN factor_ic_summary_metrics AS cross_summary
                ON cross_summary.id = validity.cross_sectional_summary_id
            WHERE validity.factor_id = %s
              AND validity.is_sub_factor_id = 1
              AND (
                  validity.time_series_summary_id IS NOT NULL
                  OR validity.cross_sectional_summary_id IS NOT NULL
              )
            ORDER BY validity.updated_at DESC, validity.id DESC
            """,
            (registration_validity_status_id, sub_factor_id),
        )

    def count_parent_relations_for_sub_factor(self, sub_factor_id: int) -> int:
        """统计登记生成子因子的母因子关联数量。

        参数 ``sub_factor_id`` 是组合报告登记接口生成的子因子主键。
        返回 ``factor_sub_factor_relations`` 中对应行数；组合子因子未分配母因子时应为零。
        """

        row = self._client.fetch_one(
            "SELECT COUNT(*) AS record_count FROM factor_sub_factor_relations WHERE sub_factor_id = %s",
            (sub_factor_id,),
        )
        return int(row["record_count"]) if row is not None else 0

    def find_existing_sub_factor_name(self) -> str | None:
        """查找一个已存在的非空子因子名称用于名称冲突测试。

        不接收参数。
        返回一个子因子名称；表为空时返回 ``None``。
        """

        row = self._client.fetch_one(
            """
            SELECT sub_factor_name
            FROM sub_factors
            WHERE sub_factor_name IS NOT NULL
              AND TRIM(sub_factor_name) <> ''
            ORDER BY id ASC
            LIMIT 1
            """
        )
        return str(row["sub_factor_name"]) if row is not None else None

    def prepare_form_for_worker(self, form_id: int, pipeline_run_id: str, lock_pool: bool = True) -> dict[str, Any]:
        """将测试创建的表单准备为初始版本 Worker 回写前置状态。

        参数 ``form_id`` 是测试表单主键，``pipeline_run_id`` 是本次模拟 Worker 运行标识，``lock_pool`` 决定是否锁定因子池。
        返回更新后的表单字典；仅允许测试环境写入，表单或因子池不存在时抛出 ``RuntimeError``。
        """

        self._assert_test_write_allowed()
        with self._client.transaction() as transaction:
            form = transaction.fetch_one(
                """
                SELECT id, factor_combo_pool_id
                FROM factor_combo_form
                WHERE id = %s
                FOR UPDATE
                """,
                (form_id,),
            )
            if form is None:
                raise RuntimeError(f"Factor combo form does not exist: {form_id}")
            pool_id = int(form["factor_combo_pool_id"])
            if lock_pool:
                result = transaction.execute(
                    "UPDATE factor_combo_pool SET status = %s WHERE pool_id = %s",
                    ("locked", pool_id),
                )
                if result.rowcount != 1:
                    raise RuntimeError(f"Factor combo pool does not exist: {pool_id}")
            transaction.execute(
                """
                UPDATE factor_combo_form
                SET status = %s,
                    pipeline_run_id = %s,
                    factor_combo_id = NULL,
                    factor_combo_experiment_info_id = NULL
                WHERE id = %s
                """,
                ("processing", pipeline_run_id, form_id),
            )
        prepared_form = self.get_form(form_id)
        if prepared_form is None:
            raise RuntimeError(f"Prepared factor combo form cannot be read: {form_id}")
        return prepared_form

    def set_form_status_for_test(self, form_id: int, status: str) -> dict[str, Any]:
        """只修改测试表单状态以构造单一前置条件冲突。

        参数 ``form_id`` 是自动化创建的表单主键，``status`` 必须是表结构允许的状态。
        返回更新后的表单字典；非测试环境、非法状态或表单不存在时抛出 ``RuntimeError``。
        """

        allowed_statuses = {"draft", "submitted", "processing", "completed", "failed"}
        if status not in allowed_statuses:
            raise ValueError(f"Unsupported factor combo form status: {status}")
        self._assert_test_write_allowed()
        result = self._client.execute(
            "UPDATE factor_combo_form SET status = %s WHERE id = %s",
            (status, form_id),
        )
        if result.rowcount != 1:
            raise RuntimeError(f"Factor combo form does not exist: {form_id}")
        form = self.get_form(form_id)
        if form is None:
            raise RuntimeError(f"Updated factor combo form cannot be read: {form_id}")
        return form

    def set_form_pipeline_run_for_test(self, form_id: int, pipeline_run_id: str) -> dict[str, Any]:
        """只修改测试表单的 Pipeline Run ID，以隔离运行关联校验场景。

        参数 ``form_id`` 是自动化创建的表单主键，``pipeline_run_id`` 是测试用的非空运行标识。
        返回更新后的表单字典；非测试环境、运行标识为空、长度超限或表单不存在时抛出异常。
        """

        normalized_pipeline_run_id = pipeline_run_id.strip()
        if not 1 <= len(normalized_pipeline_run_id) <= 255:
            raise ValueError("pipeline_run_id must contain 1 to 255 non-whitespace characters")
        self._assert_test_write_allowed()
        result = self._client.execute(
            "UPDATE factor_combo_form SET pipeline_run_id = %s WHERE id = %s",
            (normalized_pipeline_run_id, form_id),
        )
        if result.rowcount != 1:
            raise RuntimeError(f"Factor combo form does not exist: {form_id}")
        form = self.get_form(form_id)
        if form is None:
            raise RuntimeError(f"Updated factor combo form cannot be read: {form_id}")
        return form

    def set_pool_status_for_test(self, pool_id: int, status: str) -> dict[str, Any]:
        """只修改测试因子池状态以构造锁定状态冲突。

        参数 ``pool_id`` 是自动化创建的因子池主键，``status`` 必须是表结构允许的状态。
        返回更新后的因子池字典；非测试环境、非法状态或因子池不存在时抛出 ``RuntimeError``。
        """

        allowed_statuses = {"draft", "locked", "archived"}
        if status not in allowed_statuses:
            raise ValueError(f"Unsupported factor combo pool status: {status}")
        self._assert_test_write_allowed()
        result = self._client.execute(
            "UPDATE factor_combo_pool SET status = %s WHERE pool_id = %s",
            (status, pool_id),
        )
        if result.rowcount != 1:
            raise RuntimeError(f"Factor combo pool does not exist: {pool_id}")
        pool = self.get_pool(pool_id)
        if pool is None:
            raise RuntimeError(f"Updated factor combo pool cannot be read: {pool_id}")
        return pool

    def claim_feedback_for_worker(self, feedback_id: int) -> dict[str, Any]:
        """将测试创建的 pending Feedback 标记为已被模拟 Worker 认领。

        参数 ``feedback_id`` 是需要认领的反馈主键。
        返回更新后的反馈字典；仅允许测试环境写入，反馈不存在时抛出 ``RuntimeError``。
        """

        self._assert_test_write_allowed()
        result = self._client.execute(
            """
            UPDATE factor_combo_experiment_feedback
            SET status = %s,
                claimed_at = UTC_TIMESTAMP(3)
            WHERE id = %s
              AND status = %s
            """,
            ("processing", feedback_id, "pending"),
        )
        if result.rowcount != 1:
            raise RuntimeError(f"Pending factor combo feedback cannot be claimed: {feedback_id}")
        feedback = self.get_feedback(feedback_id)
        if feedback is None:
            raise RuntimeError(f"Claimed factor combo feedback cannot be read: {feedback_id}")
        return feedback

    def clean_test_graph(self, form_ids: Iterable[int], session_ids: Iterable[int]) -> None:
        """删除由自动化创建的组合因子测试数据图。

        参数 ``form_ids`` 是待清理表单主键集合，``session_ids`` 是对应空会话主键集合。
        不返回值；仅允许测试环境写入，删除顺序遵循数据库关联关系以避免遗留组合、实验、登记、子因子和刷新明细。
        ``factor_ic_runs`` 主表不在这里删除，因为它没有测试子因子归属字段，且同一个计算 Run 可能被多个因子共享；
        只清理可以按生成子因子唯一定位的明细行和 ``sub_factor_refreshes`` 任务记录。
        """

        self._assert_test_write_allowed()
        normalized_form_ids = sorted({int(form_id) for form_id in form_ids})
        normalized_session_ids = sorted({int(session_id) for session_id in session_ids})
        if not normalized_form_ids and not normalized_session_ids:
            return
        with self._client.transaction() as transaction:
            version_rows = self._fetch_versions_for_forms(transaction, normalized_form_ids)
            version_ids = [int(row["id"]) for row in version_rows]
            combo_ids = [int(row["combo_id"]) for row in version_rows if row.get("combo_id") is not None]
            experiment_ids = [
                int(row["experiment_id"])
                for row in version_rows
                if row.get("experiment_id") is not None
            ]
            form_rows = self._fetch_forms_for_cleanup(transaction, normalized_form_ids)
            experiment_ids.extend(
                int(row["factor_combo_experiment_info_id"])
                for row in form_rows
                if row.get("factor_combo_experiment_info_id") is not None
            )
            registration_rows = self._fetch_registrations(transaction, combo_ids)
            generated_sub_factor_ids = [
                int(row["sub_factor_id"])
                for row in registration_rows
                if row.get("sub_factor_id") is not None
            ]
            if self._has_active_refreshes(transaction, generated_sub_factor_ids):
                # 异步刷新尚未进入明确终态时，整组业务图都必须保留，避免删掉 Worker 仍在写入的目标。
                return

            self._delete_in(transaction, "factor_combo_registered_factor", "combo_id", combo_ids)
            self._delete_in(
                transaction,
                "factor_validity_status",
                "factor_id",
                generated_sub_factor_ids,
                suffix="AND is_sub_factor_id = 1",
            )

            # 这些表的 factor_id/sub_factor_id 指向本次登记刚创建的子因子，可以安全清理；Run 主表只保留审计记录。
            self._delete_in(
                transaction,
                "factor_ic_slice_metrics",
                "factor_id",
                generated_sub_factor_ids,
                suffix="AND is_sub_factor_id = 1",
            )
            self._delete_in(
                transaction,
                "factor_value_slice_metrics",
                "factor_id",
                generated_sub_factor_ids,
                suffix="AND is_sub_factor_id = 1",
            )
            self._delete_in(
                transaction,
                "factor_ic_summary_metrics",
                "factor_id",
                generated_sub_factor_ids,
                suffix="AND is_sub_factor_id = 1",
            )
            self._delete_in(
                transaction,
                "sub_factor_refreshes",
                "sub_factor_id",
                generated_sub_factor_ids,
            )

            self._delete_in(
                transaction,
                "factors_details",
                "factor_id",
                generated_sub_factor_ids,
                suffix="AND is_sub_factor_id = 1",
            )
            self._delete_in(transaction, "sub_factor_parent_relations", "sub_factor_id", generated_sub_factor_ids)
            self._delete_in(transaction, "factor_sub_factor_relations", "sub_factor_id", generated_sub_factor_ids)
            self._delete_in(transaction, "sub_factors", "id", generated_sub_factor_ids)
            self._delete_in(transaction, "factor_combo_experiment_feedback", "form_id", normalized_form_ids)
            self._delete_in(transaction, "factor_combo_component", "combo_id", version_ids)
            self._delete_in(transaction, "factor_combo_form", "id", normalized_form_ids, update_only=True)
            self._clear_combo_experiment_pointers(transaction, version_ids)
            self._delete_in(transaction, "factor_combo_experiment_info", "id", experiment_ids)
            self._delete_in(transaction, "factor_combo", "id", version_ids)
            self._delete_in(transaction, "factor_combo_pool_member", "factor_combo_form_id", normalized_form_ids)
            self._delete_in(transaction, "factor_combo_pool", "factor_combo_form_id", normalized_form_ids)
            self._delete_in(transaction, "factor_combo_form", "id", normalized_form_ids)
            self._delete_in(transaction, "chat_messages", "session_id", normalized_session_ids)
            self._delete_in(transaction, "chat_sessions", "id", normalized_session_ids)

    def _has_active_refreshes(
        self,
        transaction: DatabaseTransaction,
        sub_factor_ids: Sequence[int],
    ) -> bool:
        """检查待清理子因子是否仍有未进入明确终态的刷新任务。

        参数 ``transaction`` 是当前清理事务，``sub_factor_ids`` 是本次登记生成的子因子主键集合。返回 ``True`` 表示
        至少存在一个状态为空、未知或仍在运行的 ``sub_factor_refreshes`` 任务；集合为空时返回 ``False``。查询异常
        会继续向上传递，使清理事务回滚，而不是在无法确认安全性时执行删除。
        """

        if not sub_factor_ids:
            return False
        placeholders = self._placeholders(sub_factor_ids)
        terminal_statuses = (
            "completed",
            "complete",
            "success",
            "succeeded",
            "done",
            "failed",
            "partial",
            "error",
            "cancelled",
            "canceled",
            "aborted",
            "skipped",
        )
        status_placeholders = self._placeholders(terminal_statuses)
        row = transaction.fetch_one(
            f"""
            SELECT 1 AS active_refresh
            FROM sub_factor_refreshes
            WHERE sub_factor_id IN ({placeholders})
              AND (
                  status IS NULL
                  OR LOWER(TRIM(status)) NOT IN ({status_placeholders})
              )
            LIMIT 1
            """,
            tuple(sub_factor_ids) + tuple(terminal_statuses),
        )
        return row is not None

    def _fetch_versions_for_forms(
        self,
        transaction: DatabaseTransaction,
        form_ids: Sequence[int],
    ) -> list[dict[str, Any]]:
        """在清理事务中查询由表单创建的全部组合版本。

        参数 ``transaction`` 是当前数据库事务，``form_ids`` 是表单主键集合。
        返回版本字典列表；集合为空时返回空列表。
        """

        if not form_ids:
            return []
        placeholders = self._placeholders(form_ids)
        return transaction.fetch_all(
            f"SELECT id, combo_id, experiment_id FROM factor_combo WHERE initial_form_id IN ({placeholders})",
            tuple(form_ids),
        )

    def _fetch_forms_for_cleanup(
        self,
        transaction: DatabaseTransaction,
        form_ids: Sequence[int],
    ) -> list[dict[str, Any]]:
        """在清理事务中查询表单当前实验指针。

        参数 ``transaction`` 是当前数据库事务，``form_ids`` 是表单主键集合。
        返回表单字典列表；集合为空时返回空列表。
        """

        if not form_ids:
            return []
        placeholders = self._placeholders(form_ids)
        return transaction.fetch_all(
            f"SELECT id, factor_combo_experiment_info_id FROM factor_combo_form WHERE id IN ({placeholders})",
            tuple(form_ids),
        )

    def _fetch_registrations(
        self,
        transaction: DatabaseTransaction,
        combo_ids: Sequence[int],
    ) -> list[dict[str, Any]]:
        """在清理事务中查询组合登记产生的子因子。

        参数 ``transaction`` 是当前数据库事务，``combo_ids`` 是业务级组合 ID 集合。
        返回登记字典列表；集合为空时返回空列表。
        """

        if not combo_ids:
            return []
        placeholders = self._placeholders(combo_ids)
        return transaction.fetch_all(
            f"SELECT combo_id, sub_factor_id FROM factor_combo_registered_factor WHERE combo_id IN ({placeholders})",
            tuple(combo_ids),
        )

    def _delete_in(
        self,
        transaction: DatabaseTransaction,
        table_name: str,
        column_name: str,
        values: Sequence[int],
        *,
        suffix: str = "",
        update_only: bool = False,
    ) -> None:
        """在清理事务中按主键集合删除或清空表单关联。

        参数 ``transaction`` 是当前数据库事务，``table_name`` 与 ``column_name`` 来自固定内部白名单，``values`` 是绑定值，
        ``suffix`` 是固定的附加过滤条件，``update_only`` 为真时只清空表单组合和实验指针。
        不返回值；值集合为空时不执行 SQL，底层数据库错误继续向上传递。
        """

        if not values:
            return
        placeholders = self._placeholders(values)
        if update_only:
            transaction.execute(
                f"""
                UPDATE {table_name}
                SET factor_combo_id = NULL,
                    factor_combo_experiment_info_id = NULL
                WHERE {column_name} IN ({placeholders})
                """,
                tuple(values),
            )
            return
        transaction.execute(
            f"DELETE FROM {table_name} WHERE {column_name} IN ({placeholders}) {suffix}",
            tuple(values),
        )

    def _clear_combo_experiment_pointers(
        self,
        transaction: DatabaseTransaction,
        version_ids: Sequence[int],
    ) -> None:
        """在删除测试实验前解除组合版本的实验外键。

        参数 ``transaction`` 是当前清理事务，``version_ids`` 是待删除的具体组合版本主键。
        不返回值；集合为空时不执行 SQL，数据库错误继续向上传递并触发事务回滚。
        """

        if not version_ids:
            return
        placeholders = self._placeholders(version_ids)
        transaction.execute(
            f"UPDATE factor_combo SET experiment_id = NULL WHERE id IN ({placeholders})",
            tuple(version_ids),
        )

    @staticmethod
    def _placeholders(values: Sequence[Any]) -> str:
        """为固定长度的 MySQL IN 条件生成参数占位符。

        参数 ``values`` 是需要绑定的值集合。
        返回对应数量的 ``%s`` 占位符字符串；空集合时抛出 ``ValueError``，避免生成无效 SQL。
        """

        if not values:
            raise ValueError("Cannot build SQL placeholders for an empty collection")
        return ", ".join("%s" for _ in values)

    @staticmethod
    def _to_sub_factor_choice(row: Mapping[str, Any] | None) -> SubFactorChoice | None:
        """将子因子查询结果转换为 ``SubFactorChoice``。

        参数 ``row`` 是数据库返回的一行或 ``None``。
        返回转换后的选择对象；没有记录时返回 ``None``。
        """

        if row is None:
            return None
        return SubFactorChoice(
            sub_factor_id=int(row["sub_factor_id"]),
            sub_factor_name=str(row["sub_factor_name"]),
            parent_factor_id=int(row["parent_factor_id"]),
            parent_factor_name=str(row["parent_factor_name"]),
        )

    @staticmethod
    def _normalize_json_columns(row: dict[str, Any] | None, *column_names: str) -> dict[str, Any] | None:
        """将 MySQL JSON 字段统一转换为 Python 对象。

        参数 ``row`` 是数据库查询结果，``column_names`` 是需要解析的 JSON 列名。
        返回复制后的字典或 ``None``；JSON 内容无效时保留原值，便于测试直接报告数据库异常数据。
        """

        if row is None:
            return None
        result = dict(row)
        for column_name in column_names:
            value = result.get(column_name)
            if isinstance(value, str):
                try:
                    result[column_name] = json.loads(value)
                except json.JSONDecodeError:
                    pass
        return result

    def _assert_test_write_allowed(self) -> None:
        """阻止在非测试环境执行组合因子测试数据写入。

        不接收参数。
        不返回值；当前环境不是 ``test`` 时抛出 ``RuntimeError``，避免误删或伪造非测试环境业务数据。
        """

        if self._environment != "test":
            raise RuntimeError(
                f"Factor combo database writes are allowed only in the test environment, current: {self._environment!r}"
            )
