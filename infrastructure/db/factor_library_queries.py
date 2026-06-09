from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FactorListQuery:
    page: int = 1
    limit: int = 20
    factor_theme: str | None = None
    created_by: str | None = None
    operator_by: str | None = None
    factor_detail_status: int | None = None
    sort_by: str | None = None
    sort_order: str | None = None

    @property
    def offset(self) -> int:
        return max(self.page - 1, 0) * self.limit


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
    f.created_by,
    f.created_by_uid,
    f.operator_by,
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


def fetch_factor_list_db_page(client: Any, query: FactorListQuery) -> dict[str, Any]:
    where_sql, params = _build_filters(query)
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
    sort_column = SORT_COLUMNS.get(query.sort_by or "", "f.id")
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

    factors_by_id = _fetch_factors(client, factor_ids)
    details_by_factor_id = _fetch_details(client, factor_ids)
    themes_by_factor_id = _fetch_themes(client, factor_ids)

    items = []
    for factor_id in factor_ids:
        if factor_id not in factors_by_id:
            raise AssertionError(f"Missing factor row for id {factor_id}")
        factor = dict(factors_by_id[factor_id])
        factor["factor_detail"] = details_by_factor_id.get(factor_id)
        factor["themes"] = themes_by_factor_id.get(factor_id, [])
        items.append(factor)

    return {"pagination": {"page": query.page, "limit": query.limit, "total": total}, "items": items}


def _build_filters(query: FactorListQuery) -> tuple[str, dict[str, Any]]:
    joins = []
    predicates = []
    params: dict[str, Any] = {}

    if query.factor_theme is not None:
        joins.append("JOIN factor_theme_relations ftr_filter ON ftr_filter.factor_id = f.id")
        joins.append("JOIN themes t ON t.id = ftr_filter.theme_id")
        predicates.append("t.theme_key = %(factor_theme)s")
        params["factor_theme"] = query.factor_theme

    if query.factor_detail_status is not None:
        joins.append("JOIN factors_details fd ON fd.factor_id = f.id AND fd.is_sub_factor_id = 0")
        predicates.append("fd.status = %(factor_detail_status)s")
        params["factor_detail_status"] = query.factor_detail_status

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


def _fetch_factors(client: Any, factor_ids: list[Any]) -> dict[Any, dict[str, Any]]:
    rows = client.fetch_all(
        f"""
        SELECT {FACTOR_FIELDS}
        FROM factors f
        WHERE f.id IN %(factor_ids)s
        """,
        {"factor_ids": tuple(factor_ids)},
    )
    return {row["id"]: row for row in rows}


def _fetch_details(client: Any, factor_ids: list[Any]) -> dict[Any, dict[str, Any]]:
    rows = client.fetch_all(
        f"""
        SELECT {DETAIL_FIELDS}
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


def _fetch_themes(client: Any, factor_ids: list[Any]) -> dict[Any, list[dict[str, Any]]]:
    rows = client.fetch_all(
        f"""
        SELECT {THEME_FIELDS}
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
