from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


BASIC_FIELDS = (
    "id",
    "serial_number",
    "serial_prefix",
    "factor_name",
    "cn_name",
    "factor_tags",
    "level",
    "max_level",
    "child_factor_count",
    "created_by",
    "created_by_uid",
    "operator_by",
    "operator_by_uid",
)

TIME_FIELDS = ("created_at", "updated_at")

DETAIL_FIELDS = (
    "id",
    "factor_id",
    "serial_number",
    "name",
    "update_interval",
    "hit_count",
    "strategy_status",
    "status",
)

THEME_FIELDS = ("id", "theme_key", "theme_name", "cn_name", "status")


def assert_success_body(body: dict[str, Any]) -> None:
    assert isinstance(body, dict), "body must be dict"
    assert body.get("success") is True, "success must be True"
    assert "data" in body, "body must contain data"


def assert_factor_list_shape(body: dict[str, Any]) -> None:
    assert_success_body(body)
    data = body["data"]
    assert isinstance(data, dict), "data must be dict"
    assert isinstance(data.get("items"), list), "data.items must be list"
    assert isinstance(data.get("pagination"), dict), "data.pagination must be dict"

    pagination = data["pagination"]
    for field in ("page", "limit", "total"):
        assert field in pagination, f"pagination.{field} is required"
        assert isinstance(pagination[field], int), f"pagination.{field} must be int"

    required_item_fields = (
        "id",
        "serial_number",
        "factor_name",
        "cn_name",
        "factor_detail",
        "themes",
    )
    for index, item in enumerate(data["items"]):
        assert isinstance(item, dict), f"items[{index}] must be dict"
        for field in required_item_fields:
            assert field in item, f"items[{index}].{field} is required"
        assert isinstance(item["themes"], list), f"items[{index}].themes must be list"
        assert isinstance(item["factor_detail"], dict), f"items[{index}].factor_detail must be dict"


def assert_factor_list_matches_db(api_body: dict[str, Any], db_page: dict[str, Any]) -> None:
    assert_factor_list_shape(api_body)
    api_data = api_body["data"]
    api_pagination = api_data["pagination"]
    db_pagination = db_page.get("pagination", {})
    api_items = api_data["items"]
    db_items = db_page.get("items")

    assert isinstance(db_items, list), "db_page.items must be list"
    assert api_pagination["total"] == db_pagination.get("total"), "pagination.total mismatch"
    assert len(api_items) == len(db_items), "items length mismatch"

    api_ids = [item.get("id") for item in api_items]
    db_ids = [item.get("id") for item in db_items]
    assert api_ids == db_ids, f"item id order mismatch: api={api_ids}, db={db_ids}"

    for index, (api_item, db_item) in enumerate(zip(api_items, db_items, strict=True)):
        _assert_basic_fields_match(api_item, db_item, index)
        _assert_time_fields_match(api_item, db_item, index)
        _assert_factor_detail_matches(api_item["factor_detail"], db_item.get("factor_detail"), index)
        _assert_themes_match(api_item["themes"], db_item.get("themes"), index)


def assert_theme_ids_exist_in_theme_list(factor_body: dict[str, Any], themes_body: dict[str, Any]) -> None:
    assert_factor_list_shape(factor_body)
    assert_success_body(themes_body)
    themes = _extract_theme_items(themes_body["data"])
    theme_ids = {theme.get("id") for theme in themes}

    missing_messages = []
    for index, item in enumerate(factor_body["data"]["items"]):
        factor_id = item.get("id")
        for theme in item["themes"]:
            theme_id = theme.get("id")
            if theme_id not in theme_ids:
                missing_messages.append(f"items[{index}] factor_id={factor_id} missing theme_id={theme_id}")

    assert not missing_messages, "; ".join(missing_messages)


def _assert_basic_fields_match(api_item: dict[str, Any], db_item: dict[str, Any], index: int) -> None:
    for field in BASIC_FIELDS:
        assert api_item.get(field) == db_item.get(field), (
            f"items[{index}].{field} mismatch: api={api_item.get(field)!r}, db={db_item.get(field)!r}"
        )


def _assert_time_fields_match(api_item: dict[str, Any], db_item: dict[str, Any], index: int) -> None:
    for field in TIME_FIELDS:
        api_value = _normalize_utc_second(api_item.get(field))
        db_value = _normalize_utc_second(db_item.get(field))
        assert api_value == db_value, f"items[{index}].{field} mismatch: api={api_value!r}, db={db_value!r}"


def _assert_factor_detail_matches(api_detail: dict[str, Any], db_detail: Any, index: int) -> None:
    assert isinstance(db_detail, dict), f"items[{index}].factor_detail db value must be dict"
    for field in DETAIL_FIELDS:
        assert api_detail.get(field) == db_detail.get(field), (
            f"items[{index}].factor_detail.{field} mismatch: "
            f"api={api_detail.get(field)!r}, db={db_detail.get(field)!r}"
        )

    api_is_sub = _normalize_bool(api_detail.get("is_sub_factor_id"))
    db_is_sub = _normalize_bool(db_detail.get("is_sub_factor_id"))
    assert api_is_sub == db_is_sub, (
        f"items[{index}].factor_detail.is_sub_factor_id mismatch: api={api_is_sub!r}, db={db_is_sub!r}"
    )


def _assert_themes_match(api_themes: list[dict[str, Any]], db_themes: Any, index: int) -> None:
    assert isinstance(db_themes, list), f"items[{index}].themes db value must be list"
    api_by_id = _themes_by_id(api_themes, f"items[{index}].themes api")
    db_by_id = _themes_by_id(db_themes, f"items[{index}].themes db")
    assert set(api_by_id) == set(db_by_id), (
        f"items[{index}].theme_id set mismatch: api={sorted(api_by_id)}, db={sorted(db_by_id)}"
    )

    for theme_id, api_theme in api_by_id.items():
        db_theme = db_by_id[theme_id]
        for field in THEME_FIELDS:
            assert api_theme.get(field) == db_theme.get(field), (
                f"items[{index}].themes[{theme_id}].{field} mismatch: "
                f"api={api_theme.get(field)!r}, db={db_theme.get(field)!r}"
            )


def _themes_by_id(themes: list[dict[str, Any]], label: str) -> dict[Any, dict[str, Any]]:
    result = {}
    for theme in themes:
        assert isinstance(theme, dict), f"{label} item must be dict"
        theme_id = theme.get("id")
        assert theme_id not in result, f"{label} duplicate theme_id: {theme_id!r}"
        result[theme_id] = theme
    return result


def _extract_theme_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        items = data.get("items")
        assert isinstance(items, list), "themes data.items must be list"
        return items
    raise AssertionError("themes data must be list or dict with items")


def _normalize_utc_second(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        text = text.replace(" ", "T", 1)
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            dt = datetime.strptime(text, "%Y-%m-%dT%H:%M:%S")
    else:
        raise AssertionError(f"Unsupported datetime value: {value!r}")

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc).replace(microsecond=0)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    raise AssertionError(f"is_sub_factor_id must be bool or 0/1, got {value!r}")
