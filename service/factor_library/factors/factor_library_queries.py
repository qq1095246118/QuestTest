from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FactorListQuery:
    """因子列表 DB 查询参数对象。

    请求参数:
        page、limit: 分页参数。
        factor_theme、created_by、operator_by、status、factor_detail_status: 筛选参数。
        sort_by、sort_order: 排序参数。
    返回值:
        不可变的数据对象，供 DB 查询 service 生成 SQL 条件和分页偏移使用。
    """

    page: int = 1
    limit: int = 20
    factor_theme: str | None = None
    created_by: str | None = None
    operator_by: str | None = None
    status: int | None = None
    factor_detail_status: int | None = None
    sort_by: str | None = None
    sort_order: str | None = None

    @property
    def offset(self) -> int:
        """计算当前分页查询的 SQL OFFSET。

        请求参数:
            无，使用当前 FactorListQuery 实例的 page 和 limit。
        返回值:
            非负整数 OFFSET；page 小于 1 时按第一页处理。
        """
        return max(self.page - 1, 0) * self.limit


class FactorListDBService:
    """因子列表 DB 查询服务。

    请求参数:
        不需要实例化，直接通过静态方法执行只读 DB 查询。
    返回值:
        提供与因子列表接口结构对齐的 DB 分页数据查询能力。
    """

    SORT_COLUMNS = {
        "id": "f.id",
        "created_at": "f.created_at",
        "updated_at": "f.updated_at",
        "factor_name": "f.factor_name",
        "cn_name": "f.cn_name",
    }

    FACTOR_FIELDS = """
        f.id,
        f.serial_number,
        f.serial_prefix,
        f.factor_name,
        f.cn_name,
        f.factor_tags,
        f.level,
        f.max_level,
        f.child_factor_count,
        f.metadata,
        f.latest_status_updated_at,
        COALESCE(NULLIF(TRIM(created_user.display_name), ''), f.created_by) AS created_by,
        f.created_by_uid,
        COALESCE(NULLIF(TRIM(operator_user.display_name), ''), f.operator_by) AS operator_by,
        f.operator_by_uid,
        f.created_at,
        f.updated_at
    """

    DETAIL_FIELDS = """
        fd.id,
        fd.factor_id,
        fd.is_sub_factor_id,
        fd.serial_number,
        fd.name,
        fd.description,
        fd.data_source,
        fd.calc_function,
        fd.calc_logic,
        fd.params,
        fd.explanation,
        fd.update_interval,
        fd.hit_count,
        fd.strategy_status,
        fd.status,
        fd.created_at,
        fd.updated_at
    """

    THEME_FIELDS = """
        ftr.factor_id,
        t.id,
        t.theme_key,
        t.theme_name,
        t.cn_name,
        t.theme_tags,
        t.max_level,
        t.factor_count,
        t.sub_factor_count,
        t.status,
        t.created_by,
        t.created_by_uid,
        t.operator_by,
        t.operator_by_uid,
        t.created_at,
        t.updated_at
    """

    @staticmethod
    def fetch_factor_list_page(client: Any, query: FactorListQuery) -> dict[str, Any]:
        """按接口查询参数读取 DB 中的因子列表分页结果。

        请求参数:
            client: 只读 DB client，需提供 fetch_one 和 fetch_all 方法。
            query: 因子列表查询参数对象。
        返回值:
            与接口列表结构对齐的字典，包含 pagination 和 items。
        """
        where_sql, params = FactorListDBService.build_filters(query)
        total_row = client.fetch_one(
            f"""
            SELECT COUNT(DISTINCT f.id) AS total
            FROM factors f
            {where_sql}
            """,
            params,
        )
        total = int((total_row or {}).get("total") or 0)

        page_params = {**params, "limit": query.limit, "offset": query.offset}
        sort_column = FactorListDBService.SORT_COLUMNS.get(query.sort_by or "", "f.updated_at")
        sort_order = "ASC" if (query.sort_order or "").lower() == "asc" else "DESC"
        id_rows = client.fetch_all(
            f"""
            SELECT f.id AS id, {sort_column} AS sort_value
            FROM factors f
            {where_sql}
            GROUP BY f.id, sort_value
            ORDER BY sort_value {sort_order}, f.id {sort_order}
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            page_params,
        )
        factor_ids = [row["id"] for row in id_rows]
        if not factor_ids:
            return {"pagination": {"page": query.page, "limit": query.limit, "total": total}, "items": []}

        factors_by_id = FactorListDBService.fetch_factors(client, factor_ids)
        details_by_factor_id = FactorListDBService.fetch_details(client, factor_ids)
        themes_by_factor_id = FactorListDBService.fetch_themes(client, factor_ids)

        items = []
        for factor_id in factor_ids:
            if factor_id not in factors_by_id:
                raise AssertionError(f"Missing factor row for id {factor_id}")
            factor = dict(factors_by_id[factor_id])
            factor["factor_detail"] = details_by_factor_id.get(factor_id)
            factor["themes"] = themes_by_factor_id.get(factor_id, [])
            items.append(factor)

        return {"pagination": {"page": query.page, "limit": query.limit, "total": total}, "items": items}

    @staticmethod
    def build_filters(query: FactorListQuery) -> tuple[str, dict[str, Any]]:
        """根据因子列表查询参数生成 SQL 过滤条件。

        请求参数:
            query: 因子列表查询参数对象。
        返回值:
            二元组，第一项是 JOIN/WHERE SQL 片段，第二项是 SQL 参数字典。
        """
        joins = []
        predicates = []
        params: dict[str, Any] = {}

        if query.factor_theme is not None:
            joins.append("JOIN factor_theme_relations ftr_filter ON ftr_filter.factor_id = f.id")
            joins.append("JOIN themes t ON t.id = ftr_filter.theme_id")
            predicates.append("t.theme_key = %(factor_theme)s")
            params["factor_theme"] = query.factor_theme

        detail_status = query.factor_detail_status if query.factor_detail_status is not None else query.status
        if detail_status is not None:
            joins.append("JOIN factors_details fd ON fd.factor_id = f.id AND fd.is_sub_factor_id = 0")
            predicates.append("fd.status = %(factor_detail_status)s")
            params["factor_detail_status"] = detail_status

        if query.created_by is not None:
            predicates.append("f.created_by = %(created_by)s")
            params["created_by"] = query.created_by

        if query.operator_by is not None:
            predicates.append("f.operator_by = %(operator_by)s")
            params["operator_by"] = query.operator_by

        where_parts = [*joins]
        if predicates:
            where_parts.append("WHERE " + " AND ".join(predicates))
        return "\n".join(where_parts), params

    @staticmethod
    def fetch_factors(client: Any, factor_ids: list[Any]) -> dict[Any, dict[str, Any]]:
        """批量读取指定因子的基础字段。

        请求参数:
            client: 只读 DB client。
            factor_ids: 因子 ID 列表。
        返回值:
            以 factor_id 为 key 的因子基础字段字典。
        """
        rows = client.fetch_all(
            f"""
            SELECT {FactorListDBService.FACTOR_FIELDS}
            FROM factors f
            LEFT JOIN app_users created_user ON created_user.id = f.created_by_uid
            LEFT JOIN app_users operator_user ON operator_user.id = f.operator_by_uid
            WHERE f.id IN %(factor_ids)s
            """,
            {"factor_ids": tuple(factor_ids)},
        )
        return {row["id"]: row for row in rows}

    @staticmethod
    def fetch_details(client: Any, factor_ids: list[Any]) -> dict[Any, dict[str, Any]]:
        """批量读取指定因子的主因子详情字段。

        请求参数:
            client: 只读 DB client。
            factor_ids: 因子 ID 列表。
        返回值:
            以 factor_id 为 key 的详情字段字典。
        """
        rows = client.fetch_all(
            f"""
            SELECT {FactorListDBService.DETAIL_FIELDS}
            FROM factors_details fd
            WHERE fd.factor_id IN %(factor_ids)s
              AND fd.is_sub_factor_id = 0
            ORDER BY fd.factor_id ASC, fd.updated_at DESC, fd.id DESC
            """,
            {"factor_ids": tuple(factor_ids)},
        )
        details_by_factor_id: dict[Any, dict[str, Any]] = {}
        for row in rows:
            details_by_factor_id.setdefault(row["factor_id"], row)
        return details_by_factor_id

    @staticmethod
    def fetch_themes(client: Any, factor_ids: list[Any]) -> dict[Any, list[dict[str, Any]]]:
        """批量读取指定因子的主题归属字段。

        请求参数:
            client: 只读 DB client。
            factor_ids: 因子 ID 列表。
        返回值:
            以 factor_id 为 key 的主题列表字典。
        """
        rows = client.fetch_all(
            f"""
            SELECT {FactorListDBService.THEME_FIELDS}
            FROM factor_theme_relations ftr
            JOIN themes t ON t.id = ftr.theme_id
            WHERE ftr.factor_id IN %(factor_ids)s
            ORDER BY ftr.factor_id ASC, t.id ASC
            """,
            {"factor_ids": tuple(factor_ids)},
        )
        themes_by_factor_id: dict[Any, list[dict[str, Any]]] = {}
        for row in rows:
            factor_id = row["factor_id"]
            theme = dict(row)
            del theme["factor_id"]
            themes_by_factor_id.setdefault(factor_id, []).append(theme)
        return themes_by_factor_id
