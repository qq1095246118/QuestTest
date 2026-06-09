from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from infrastructure.assertions.factor_library_asserts import (
    assert_factor_list_matches_db,
    assert_factor_list_shape,
    assert_success_body,
    assert_theme_ids_exist_in_theme_list,
)


def _api_item() -> dict:
    return {
        "id": 615,
        "serial_number": "F001",
        "serial_prefix": "F",
        "factor_name": "momentum",
        "cn_name": "动量",
        "factor_tags": ["trend"],
        "level": 1,
        "max_level": 3,
        "child_factor_count": 2,
        "created_by": "alice",
        "created_by_uid": "uid-alice",
        "operator_by": "bob",
        "operator_by_uid": "uid-bob",
        "created_at": "2026-06-06T14:00:01Z",
        "updated_at": "2026-06-06T14:00:01.456Z",
        "factor_detail": {
            "id": 901,
            "factor_id": 615,
            "is_sub_factor_id": False,
            "serial_number": "FD001",
            "name": "momentum_detail",
            "update_interval": "1h",
            "hit_count": 9,
            "strategy_status": 1,
            "status": 1,
        },
        "themes": [
            {
                "id": 2,
                "theme_key": "trend",
                "theme_name": "Trend",
                "cn_name": "趋势",
                "status": 1,
            },
            {
                "id": 1,
                "theme_key": "momentum",
                "theme_name": "Momentum",
                "cn_name": "动量",
                "status": 1,
            },
        ],
    }


def _db_item() -> dict:
    return {
        "id": 615,
        "serial_number": "F001",
        "serial_prefix": "F",
        "factor_name": "momentum",
        "cn_name": "动量",
        "factor_tags": ["trend"],
        "level": 1,
        "max_level": 3,
        "child_factor_count": 2,
        "created_by": "alice",
        "created_by_uid": "uid-alice",
        "operator_by": "bob",
        "operator_by_uid": "uid-bob",
        "created_at": "2026-06-06 14:00:01",
        "updated_at": "2026-06-06 14:00:01",
        "factor_detail": {
            "id": 901,
            "factor_id": 615,
            "is_sub_factor_id": 0,
            "serial_number": "FD001",
            "name": "momentum_detail",
            "update_interval": "1h",
            "hit_count": 9,
            "strategy_status": 1,
            "status": 1,
        },
        "themes": [
            {
                "id": 1,
                "theme_key": "momentum",
                "theme_name": "Momentum",
                "cn_name": "动量",
                "status": 1,
            },
            {
                "id": 2,
                "theme_key": "trend",
                "theme_name": "Trend",
                "cn_name": "趋势",
                "status": 1,
            },
        ],
    }


def _api_body() -> dict:
    return {
        "success": True,
        "data": {
            "pagination": {"page": 1, "limit": 20, "total": 1},
            "items": [_api_item()],
        },
    }


def _db_page() -> dict:
    return {
        "pagination": {"page": 1, "limit": 20, "total": 1},
        "items": [_db_item()],
    }


def test_assert_success_body_accepts_success_true():
    assert_success_body({"success": True, "data": {}})


def test_assert_factor_list_shape_accepts_expected_shape():
    assert_factor_list_shape(_api_body())


def test_assert_factor_list_matches_db_accepts_full_match_with_normalized_time_and_bool():
    assert_factor_list_matches_db(_api_body(), _db_page())


@pytest.mark.parametrize(
    ("created_at", "updated_at"),
    [
        ("2026-06-06 14:00:01.123456", "2026-06-06 14:00:01+00:00"),
        ("2026-06-06 14:00:01.123456+00:00", "2026-06-06 14:00:01.999999+00:00"),
    ],
)
def test_assert_factor_list_matches_db_accepts_db_microsecond_and_offset_time_strings(created_at, updated_at):
    db_page = _db_page()
    db_page["items"][0]["created_at"] = created_at
    db_page["items"][0]["updated_at"] = updated_at

    assert_factor_list_matches_db(_api_body(), db_page)


def test_assert_factor_list_matches_db_accepts_datetime_objects():
    db_page = _db_page()
    db_page["items"][0]["created_at"] = datetime(2026, 6, 6, 14, 0, 1, 123456)
    db_page["items"][0]["updated_at"] = datetime(2026, 6, 6, 14, 0, 1, 999999, tzinfo=timezone.utc)

    assert_factor_list_matches_db(_api_body(), db_page)


def test_assert_factor_list_matches_db_rejects_string_is_sub_factor_id():
    db_page = _db_page()
    db_page["items"][0]["factor_detail"]["is_sub_factor_id"] = "0"

    with pytest.raises(AssertionError, match="is_sub_factor_id"):
        assert_factor_list_matches_db(_api_body(), db_page)


def test_assert_factor_list_matches_db_reports_basic_field_mismatch():
    api_body = _api_body()
    db_page = _db_page()
    db_page["items"][0]["child_factor_count"] = 3

    with pytest.raises(AssertionError, match="child_factor_count"):
        assert_factor_list_matches_db(api_body, db_page)


def test_assert_factor_list_matches_db_accepts_themes_in_different_order():
    api_body = _api_body()
    api_body["data"]["items"][0]["themes"] = list(reversed(api_body["data"]["items"][0]["themes"]))

    assert_factor_list_matches_db(api_body, _db_page())


def test_assert_factor_list_matches_db_reports_theme_id_mismatch():
    api_body = _api_body()
    db_page = _db_page()
    db_page["items"][0]["themes"][0]["id"] = 9

    with pytest.raises(AssertionError, match="theme_id|theme id"):
        assert_factor_list_matches_db(api_body, db_page)


def test_assert_theme_ids_exist_in_theme_list_accepts_data_list_and_data_items():
    factor_body = _api_body()

    assert_theme_ids_exist_in_theme_list(
        factor_body,
        {
            "success": True,
            "data": [
                {"id": 1, "theme_key": "momentum"},
                {"id": 2, "theme_key": "trend"},
            ],
        },
    )
    assert_theme_ids_exist_in_theme_list(
        factor_body,
        {
            "success": True,
            "data": {
                "items": [
                    {"id": 1, "theme_key": "momentum"},
                    {"id": 2, "theme_key": "trend"},
                ]
            },
        },
    )


def test_assert_theme_ids_exist_in_theme_list_reports_missing_theme_id():
    factor_body = deepcopy(_api_body())

    with pytest.raises(AssertionError, match=r"items\[0\].*615.*missing.*theme_id"):
        assert_theme_ids_exist_in_theme_list(
            factor_body,
            {"success": True, "data": [{"id": 1, "theme_key": "momentum"}]},
        )
