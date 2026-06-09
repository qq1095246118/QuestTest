from __future__ import annotations

from pathlib import Path
from typing import Any

import allure
import pytest
import yaml
from requests.exceptions import HTTPError

from api.platform.auth_api import AuthAPI
from api.platform.factor_library_api import FactorLibraryAPI
from config.settings import settings
from infrastructure.assertions.factor_library_asserts import (
    assert_factor_list_matches_db,
    assert_factor_list_shape,
    assert_success_body,
    assert_theme_ids_exist_in_theme_list,
)
from infrastructure.db.factor_library_queries import FactorListQuery, fetch_factor_list_db_page
from infrastructure.db.mysql_client import ReadOnlyMySQLClient
from infrastructure.db.ssh_tunnel import open_database_endpoint


DATA_FILE = Path(__file__).resolve().parents[3] / "data" / "factor_library_api_cases.yaml"


def _load_case(name: str) -> dict[str, Any]:
    data = yaml.safe_load(DATA_FILE.read_text(encoding="utf-8"))
    return dict(data["factor_list"][name])


def _response_from_http_error(exc: HTTPError):
    assert exc.response is not None
    return exc.response


@pytest.fixture(scope="module")
def token() -> str:
    if not settings.base_url:
        pytest.skip("Factor Library API BASE_URL is not configured.")
    if not settings.factor_email or not settings.factor_password:
        pytest.skip("Factor Library login account is not configured.")

    response = AuthAPI().login()
    assert response.status_code == 200
    body = response.json()
    assert_success_body(body)
    token_value = body["data"].get("token")
    assert token_value, f"login response missing token: {body}"
    return token_value


@pytest.fixture(scope="module")
def factor_api(token: str) -> FactorLibraryAPI:
    return FactorLibraryAPI(token=token)


@pytest.fixture(scope="module")
def db_client():
    required = [
        settings.factor_db_host,
        settings.factor_db_name,
        settings.factor_db_user,
        settings.factor_db_password,
    ]
    if not all(required):
        pytest.skip("Factor Library DB config is not complete.")

    with open_database_endpoint(settings) as endpoint:
        client = ReadOnlyMySQLClient.from_settings(host=endpoint.host, port=endpoint.port)
        try:
            yield client
        finally:
            client.close()


@allure.title("AU-01 有效账号登录成功")
@pytest.mark.factor_library_api
def test_au_01_login_success():
    """
    Case ID: AU-01
    测试目的: 使用有效账号登录因子库后端，验证返回 token 且用户邮箱与配置一致。
    """
    if not settings.base_url:
        pytest.skip("Factor Library API BASE_URL is not configured.")
    if not settings.factor_email or not settings.factor_password:
        pytest.skip("Factor Library login account is not configured.")

    response = AuthAPI().login()
    assert response.status_code == 200
    body = response.json()
    assert_success_body(body)
    assert body["data"].get("token")
    assert body["data"]["user"]["email"] == settings.factor_email


@allure.title("AU-02 错误密码登录失败")
@pytest.mark.factor_library_api
def test_au_02_login_wrong_password_fails():
    """
    Case ID: AU-02
    测试目的: 使用错误密码登录时返回鉴权失败，不返回 token。
    """
    if not settings.base_url:
        pytest.skip("Factor Library API BASE_URL is not configured.")
    if not settings.factor_email:
        pytest.skip("Factor Library login account is not configured.")

    try:
        response = AuthAPI().login(email=settings.factor_email, password="wrong-password-for-api-test")
    except HTTPError as exc:
        response = _response_from_http_error(exc)

    assert response.status_code in {400, 401, 403}
    body = response.json() if response.content else {}
    assert "token" not in str(body).lower()


@allure.title("AU-03 未带 token 查询因子列表")
@pytest.mark.factor_library_api
def test_au_03_list_factors_without_token_is_unauthorized():
    """
    Case ID: AU-03
    测试目的: 未带 Authorization 访问因子列表时返回未授权错误。
    """
    if not settings.base_url:
        pytest.skip("Factor Library API BASE_URL is not configured.")

    try:
        response = FactorLibraryAPI().list_factors(page=1, limit=5)
    except HTTPError as exc:
        response = _response_from_http_error(exc)

    assert response.status_code in {401, 403}


@allure.title("AU-04 使用无效 token 查询因子列表")
@pytest.mark.factor_library_api
def test_au_04_list_factors_invalid_token_is_unauthorized():
    """
    Case ID: AU-04
    测试目的: 使用伪造 token 访问因子列表时返回未授权错误。
    """
    if not settings.base_url:
        pytest.skip("Factor Library API BASE_URL is not configured.")

    try:
        response = FactorLibraryAPI(token="invalid-token").list_factors(page=1, limit=5)
    except HTTPError as exc:
        response = _response_from_http_error(exc)

    assert response.status_code in {401, 403}


@allure.title("FA-01 默认第一页因子列表与 DB 一致")
@pytest.mark.factor_library_api
@pytest.mark.live_db
def test_fa_01_default_first_page_matches_db(factor_api, db_client):
    """
    Case ID: FA-01
    测试目的: 查询默认第一页因子列表，验证响应结构、分页和 DB 数据一致。
    """
    params = _load_case("default")
    body = factor_api.list_factors(**params).json()
    db_page = fetch_factor_list_db_page(db_client, FactorListQuery(**params))
    assert_factor_list_matches_db(body, db_page)


@allure.title("FA-03 第二页因子列表与第一页不重复且与 DB 一致")
@pytest.mark.factor_library_api
@pytest.mark.live_db
def test_fa_03_page_two_does_not_overlap_first_page_and_matches_db(factor_api, db_client):
    """
    Case ID: FA-03
    测试目的: 查询第二页因子列表，验证与第一页 id 不重复且第二页与 DB 一致。
    """
    first_params = _load_case("default")
    second_params = _load_case("page_two")

    first_body = factor_api.list_factors(**first_params).json()
    second_body = factor_api.list_factors(**second_params).json()
    assert_factor_list_shape(first_body)
    assert_factor_list_shape(second_body)

    first_ids = {item["id"] for item in first_body["data"]["items"]}
    second_ids = {item["id"] for item in second_body["data"]["items"]}
    assert first_ids.isdisjoint(second_ids), f"page id overlap: {sorted(first_ids & second_ids)}"

    db_page = fetch_factor_list_db_page(db_client, FactorListQuery(**second_params))
    assert_factor_list_matches_db(second_body, db_page)


@allure.title("FA-05 按 updated_at 升序查询因子列表与 DB 一致")
@pytest.mark.factor_library_api
@pytest.mark.live_db
def test_fa_05_sort_updated_at_asc_matches_db(factor_api, db_client):
    """
    Case ID: FA-05
    测试目的: 按 updated_at 升序查询因子列表，验证接口顺序与 DB 一致。
    """
    params = _load_case("sort_updated_at_asc")
    body = factor_api.list_factors(**params).json()
    db_page = fetch_factor_list_db_page(db_client, FactorListQuery(**params))
    assert_factor_list_matches_db(body, db_page)


@allure.title("FA-06 按 updated_at 降序查询因子列表与 DB 一致")
@pytest.mark.factor_library_api
@pytest.mark.live_db
def test_fa_06_sort_updated_at_desc_matches_db(factor_api, db_client):
    """
    Case ID: FA-06
    测试目的: 按 updated_at 降序查询因子列表，验证接口顺序与 DB 一致。
    """
    params = _load_case("sort_updated_at_desc")
    body = factor_api.list_factors(**params).json()
    db_page = fetch_factor_list_db_page(db_client, FactorListQuery(**params))
    assert_factor_list_matches_db(body, db_page)


@allure.title("FA-07 按主题筛选因子列表与 DB 一致")
@pytest.mark.factor_library_api
@pytest.mark.live_db
def test_fa_07_filter_by_theme_matches_db(factor_api, db_client):
    """
    Case ID: FA-07
    测试目的: 使用第一页真实 theme_key 做动态主题筛选，验证接口和 DB 一致。
    """
    seed_body = factor_api.list_factors(**_load_case("default")).json()
    assert_factor_list_shape(seed_body)
    seed_items = seed_body["data"]["items"]
    if not seed_items:
        pytest.skip("Factor list first page is empty; cannot derive theme filter.")

    theme_key = None
    for item in seed_items:
        themes = item.get("themes") or []
        if themes:
            theme_key = themes[0].get("theme_key")
            break
    if not theme_key:
        pytest.skip("Factor list first page has no theme_key; cannot derive theme filter.")

    params = {"page": 1, "limit": 5, "factor_theme": theme_key}
    body = factor_api.list_factors(**params).json()
    db_page = fetch_factor_list_db_page(db_client, FactorListQuery(**params))
    assert_factor_list_matches_db(body, db_page)


@allure.title("FA-08 按 factor_detail_status=1 筛选因子列表与 DB 一致")
@pytest.mark.factor_library_api
@pytest.mark.live_db
def test_fa_08_filter_by_factor_detail_status_matches_db(factor_api, db_client):
    """
    Case ID: FA-08
    测试目的: 使用 factor_detail_status=1 筛选因子列表，验证接口和 DB 一致。
    """
    params = {"page": 1, "limit": 5, "factor_detail_status": 1}
    body = factor_api.list_factors(**params).json()
    db_page = fetch_factor_list_db_page(db_client, FactorListQuery(**params))
    assert_factor_list_matches_db(body, db_page)


@allure.title("FA-11 page=0 查询因子列表不返回 500")
@pytest.mark.factor_library_api
def test_fa_11_page_zero_does_not_return_500(factor_api):
    """
    Case ID: FA-11
    测试目的: page=0 时接口返回明确参数错误或合法修正结果，不返回 500。
    """
    try:
        response = factor_api.list_factors(**_load_case("invalid_page_zero"))
    except HTTPError as exc:
        response = _response_from_http_error(exc)

    assert response.status_code < 500


@allure.title("FA-12 limit=501 查询因子列表不返回 500")
@pytest.mark.factor_library_api
def test_fa_12_limit_too_large_does_not_return_500(factor_api):
    """
    Case ID: FA-12
    测试目的: limit=501 时接口返回明确参数错误或合法限制结果，不返回 500。
    """
    try:
        response = factor_api.list_factors(**_load_case("invalid_limit_too_large"))
    except HTTPError as exc:
        response = _response_from_http_error(exc)

    assert response.status_code < 500


@allure.title("FA-13 sort_order=bad 查询因子列表不返回 500")
@pytest.mark.factor_library_api
def test_fa_13_invalid_sort_order_does_not_return_500(factor_api):
    """
    Case ID: FA-13
    测试目的: sort_order=bad 时接口返回明确参数错误或合法默认排序结果，不返回 500。
    """
    try:
        response = factor_api.list_factors(**_load_case("invalid_sort_order"))
    except HTTPError as exc:
        response = _response_from_http_error(exc)

    assert response.status_code < 500


@allure.title("DC-02 因子列表主题存在于主题列表")
@pytest.mark.factor_library_api
def test_dc_02_factor_themes_exist_in_theme_list(factor_api):
    """
    Case ID: DC-02
    测试目的: 验证因子列表返回的主题均存在于主题列表接口。
    """
    factor_body = factor_api.list_factors(**_load_case("default")).json()
    themes_body = factor_api.list_themes().json()
    assert_theme_ids_exist_in_theme_list(factor_body, themes_body)
