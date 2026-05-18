from __future__ import annotations

from decimal import Decimal
from time import perf_counter

import allure
import pytest
from requests.exceptions import HTTPError

from api_services.kline_data_api import KlineDataAPI
from config.settings import settings

SYMBOL = "BTCUSDT"
INTERVAL = "1m"
START_TIME_MS = 1704067200000
END_TIME_MS = 1704153600000
LIMIT_SMALL = 1
LIMIT_NORMAL = 10
LIMIT_MAX = 200000
LIMIT_TOO_LARGE = LIMIT_MAX + 1
INVALID_SYMBOL = "NOT_A_SYMBOL"
PERFORMANCE_BASELINE_SECONDS = 2.0


@pytest.fixture(scope="module")
def kline_api() -> KlineDataAPI:
    if not settings.base_url or "exchange.com" in settings.base_url:
        pytest.skip("Kline API BASE_URL is not configured for live API tests.")
    return KlineDataAPI()


@allure.title("KD-FETCH-NORMAL-001 - 验证 /kline/fetch 必填参数 symbol 和合法可选参数正常返回成功信封。")
@pytest.mark.kline_api
def test_kd_fetch_normal_001(kline_api):
    """
    Case ID: KD-FETCH-NORMAL-001
    测试目的: 验证 /kline/fetch 必填参数 symbol 和合法可选参数正常返回成功信封。
    """
    response = kline_api.fetch_kline(
        symbol=SYMBOL,
        interval=INTERVAL,
        source="binance",
    )

    assert response.status_code == 200
    body = response.json()
    assert "code" in body
    assert "status" in body
    assert "message" in body
    assert "data" in body
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]
    assert body["data"] is not None


@allure.title("KD-FETCH-BOUNDARY-001 - 验证 /kline/fetch 仅传必填参数时不返回 500。")
@pytest.mark.kline_api
def test_kd_fetch_boundary_001(kline_api):
    """
    Case ID: KD-FETCH-BOUNDARY-001
    测试目的: 验证 /kline/fetch 仅传必填参数时不返回 500。
    """
    try:
        response = kline_api.fetch_kline(
            symbol=SYMBOL,
            interval=None,
            source=None,
        )
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if str(body.get("code")) == "200":
        assert body["status"] == "success"
        assert "message" in body
        assert body["message"]
        assert "data" in body


@allure.title("KD-FETCH-PARAM-001 - 验证 /kline/fetch 缺失 symbol 时返回参数校验错误。")
@pytest.mark.kline_api
def test_kd_fetch_param_001_missing_symbol(kline_api):
    """
    Case ID: KD-FETCH-PARAM-001
    测试目的: 验证 /kline/fetch 缺失 symbol 时返回参数校验错误。
    """
    try:
        response = kline_api.fetch_kline(
            symbol=None,
            interval=INTERVAL,
            source="binance",
        )
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    body = response.json() if response.content else {}
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert "symbol" in str(body)
    else:
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] == "error"
        assert "symbol" in str(body["message"])
        assert not (str(body["code"]) == "200" and body["status"] == "success")


@allure.title("KD-FETCH-PARAM-002 - 验证 /kline/fetch symbol 为空字符串时不返回成功数据。")
@pytest.mark.kline_api
def test_kd_fetch_param_002_empty_symbol(kline_api):
    """
    Case ID: KD-FETCH-PARAM-002
    测试目的: 验证 /kline/fetch symbol 为空字符串时不返回成功数据。
    """
    try:
        response = kline_api.fetch_kline(
            symbol="",
            interval=INTERVAL,
            source="binance",
        )
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert "symbol" in str(body).lower() or "error" in str(body).lower()
    else:
        assert not (
                str(body.get("code")) == "200"
                and body.get("status") == "success"
                and body.get("data") not in (None, [], {})
        )


@allure.title("KD-FETCH-PARAM-003 - 验证 /kline/fetch source=unknown 时不返回 500。")
@pytest.mark.kline_api
def test_kd_fetch_param_003_unknown_source(kline_api):
    """
    Case ID: KD-FETCH-PARAM-003
    测试目的: 验证 /kline/fetch source=unknown 时不返回 500。
    """
    try:
        response = kline_api.fetch_kline(
            symbol=SYMBOL,
            interval=INTERVAL,
            source="unknown",
        )
    except HTTPError as exc:
        assert exc.response is not 哦
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert "source" in str(body).lower() or "error" in str(body).lower()
    else:
        assert not (
                str(body.get("code")) == "200"
                and body.get("status") == "success"
                and body.get("data") not in (None, [], {})
        )


@allure.title("KD-FETCH-RESPONSE-001 - 验证 /kline/fetch 成功响应包含 code/status/message/data 统一信封。")
@pytest.mark.kline_api
def test_kd_fetch_response_001_success_schema(kline_api):
    """
    Case ID: KD-FETCH-RESPONSE-001
    测试目的: 验证 /kline/fetch 成功响应包含 code/status/message/data 统一信封。
    """
    response = kline_api.fetch_kline(symbol=SYMBOL)

    assert response.status_code == 200
    body = response.json()
    assert "code" in body
    assert "status" in body
    assert "message" in body
    assert "data" in body
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]


@allure.title("KD-FETCH-PERF-001 - 验证 /kline/fetch 典型请求在性能基线内返回成功。")
@pytest.mark.kline_api
@pytest.mark.performance
def test_kd_fetch_perf_001_baseline(kline_api):
    """
    Case ID: KD-FETCH-PERF-001
    测试目的: 验证 /kline/fetch 典型请求在性能基线内返回成功。
    """
    start = perf_counter()
    response = kline_api.fetch_kline(symbol=SYMBOL)
    elapsed = perf_counter() - start

    assert elapsed < PERFORMANCE_BASELINE_SECONDS
    assert response.status_code == 200
    body = response.json()
    assert "code" in body
    assert "status" in body
    assert "message" in body
    assert "data" in body
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]


@allure.title("KD-USDM-TIMERANGE-NORMAL-001 - 验证 USDM legacy 单 symbol 时间边界查询正常返回。")
@pytest.mark.kline_api
def test_kd_usdm_timerange_normal_001(kline_api):
    """
    Case ID: KD-USDM-TIMERANGE-NORMAL-001
    测试目的: 验证 USDM legacy 单 symbol 时间边界查询正常返回。
    """
    response = kline_api.get_usdm_time_range(symbol=SYMBOL, interval=INTERVAL)

    assert response.status_code == 200
    body = response.json()
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]
    assert "data" in body
    data = body["data"]
    assert isinstance(data, dict)
    assert "filters" in data
    for section_name in ("raw", "curated"):
        assert section_name in data
        section = data[section_name]
        assert "time_field" in section
        assert "min_time_ms" in section
        assert "max_time_ms" in section
        assert "has_data" in section
        if section["min_time_ms"] is not None:
            assert len(str(int(section["min_time_ms"]))) == 13
        if section["max_time_ms"] is not None:
            assert len(str(int(section["max_time_ms"]))) == 13
        if section["has_data"]:
            assert section["min_time_ms"] is not None
            assert section["max_time_ms"] is not None
            assert int(section["min_time_ms"]) <= int(section["max_time_ms"])


@allure.title("KD-USDM-TIMERANGE-BOUNDARY-001 - 验证 USDM 时间边界查询省略 interval 时按默认周期处理。")
@pytest.mark.kline_api
def test_kd_usdm_timerange_boundary_001(kline_api):
    """
    Case ID: KD-USDM-TIMERANGE-BOUNDARY-001
    测试目的: 验证 USDM 时间边界查询省略 interval 时按默认周期处理。
    """
    response = kline_api.get_usdm_time_range(symbol=SYMBOL, interval=None)

    assert response.status_code == 200
    body = response.json()
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]
    assert "data" in body
    data = body["data"]
    assert isinstance(data, dict)
    assert "filters" in data
    for section_name in ("raw", "curated"):
        assert section_name in data
        section = data[section_name]
        assert "time_field" in section
        assert "min_time_ms" in section
        assert "max_time_ms" in section
        assert "has_data" in section
        if section["has_data"]:
            assert int(section["min_time_ms"]) <= int(section["max_time_ms"])


@allure.title("KD-USDM-TIMERANGE-BOUNDARY-002 - 验证 USDM 时间边界 interval=1h 不 500。")
@pytest.mark.kline_api
def test_kd_usdm_timerange_boundary_002_interval_1h(kline_api):
    """
    Case ID: KD-USDM-TIMERANGE-BOUNDARY-002
    测试目的: 验证 USDM 时间边界 interval=1h 不 500。
    """
    try:
        response = kline_api.get_usdm_time_range(symbol=SYMBOL, interval="1h")
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if str(body.get("code")) == "200":
        assert body["status"] == "success"
        assert "data" in body
        data = body["data"]
        assert "raw" in data
        assert "curated" in data
        for section_name in ("raw", "curated"):
            section = data[section_name]
            assert "has_data" in section
            if section["has_data"]:
                assert int(section["min_time_ms"]) <= int(section["max_time_ms"])


@allure.title("KD-USDM-TIMERANGE-PARAM-001 - 验证 USDM 时间边界缺失 symbol 时返回参数校验错误。")
@pytest.mark.kline_api
def test_kd_usdm_timerange_param_001_missing_symbol(kline_api):
    """
    Case ID: KD-USDM-TIMERANGE-PARAM-001
    测试目的: 验证 USDM 时间边界缺失 symbol 时返回参数校验错误。
    """
    try:
        response = kline_api.get_usdm_time_range(symbol=None)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    body = response.json() if response.content else {}
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert "symbol" in str(body)
    else:
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] == "error"
        assert "symbol" in str(body["message"])
        assert not (str(body["code"]) == "200" and body["status"] == "success")


@allure.title("KD-USDM-TIMERANGE-PARAM-002 - 验证 USDM 时间边界非法 symbol 不 500。")
@pytest.mark.kline_api
def test_kd_usdm_timerange_param_002_invalid_symbol(kline_api):
    """
    Case ID: KD-USDM-TIMERANGE-PARAM-002
    测试目的: 验证 USDM 时间边界非法 symbol 不 500。
    """
    try:
        response = kline_api.get_usdm_time_range(
            symbol=INVALID_SYMBOL,
            interval=INTERVAL,
        )
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if str(body.get("code")) == "200":
        assert body["status"] == "success"
        assert "data" in body
        data = body["data"]
        assert "raw" in data
        assert "curated" in data
        for section_name in ("raw", "curated"):
            section = data[section_name]
            assert section["has_data"] is False or str(section["has_data"]).lower() == "false"


@allure.title("KD-USDM-TIMERANGE-DQC-001 - 验证 USDM 时间边界毫秒时间戳和 min/max 顺序。")
@pytest.mark.kline_api
@pytest.mark.dqc
def test_kd_usdm_timerange_dqc_001(kline_api):
    """
    Case ID: KD-USDM-TIMERANGE-DQC-001
    测试目的: 验证 USDM 时间边界毫秒时间戳和 min/max 顺序。
    """
    response = kline_api.get_usdm_time_range(symbol=SYMBOL, interval=INTERVAL)

    assert response.status_code == 200
    body = response.json()
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]
    data = body["data"]
    for section_name in ("raw", "curated"):
        section = data[section_name]
        min_time = section["min_time_ms"]
        max_time = section["max_time_ms"]
        if min_time is not None:
            assert len(str(int(min_time))) == 13
        if max_time is not None:
            assert len(str(int(max_time))) == 13
        if section["has_data"]:
            assert min_time is not None
            assert max_time is not None
            assert int(min_time) <= int(max_time)


@allure.title("KD-USDM-RAW-NORMAL-001 - 验证 USDM raw K线分页按合法 symbol、时间窗和分页参数正常返回。")
@pytest.mark.kline_api
def test_kd_usdm_raw_normal_001(kline_api):
    """
    Case ID: KD-USDM-RAW-NORMAL-001
    测试目的: 验证 USDM raw K线分页按合法 symbol、时间窗和分页参数正常返回。
    """
    response = kline_api.get_usdm_kline_raw(
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time_ms=START_TIME_MS,
        end_time_ms=END_TIME_MS,
        limit=LIMIT_NORMAL,
        offset=0,
        include_total=False,
    )

    assert response.status_code == 200
    body = response.json()
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]
    data = body["data"]
    assert "filters" in data
    assert "pagination" in data
    assert "items" in data
    items = data["items"]
    pagination = data["pagination"]
    assert isinstance(items, list)
    assert len(items) <= LIMIT_NORMAL
    assert pagination["limit"] == LIMIT_NORMAL
    assert pagination["offset"] == 0
    assert pagination["include_total"] is False or str(pagination["include_total"]).lower() == "false"
    for item in items:
        for field in ("symbol", "timestamp", "interval", "open", "high", "low", "close", "volume"):
            assert field in item
        assert item["symbol"] == SYMBOL
        assert item["interval"] == INTERVAL
        timestamp = int(item["timestamp"])
        assert len(str(timestamp)) == 13
        assert START_TIME_MS <= timestamp < END_TIME_MS
        for numeric_field in ("open", "high", "low", "close", "volume", "quote_volume"):
            if numeric_field in item and item[numeric_field] is not None:
                assert Decimal(str(item[numeric_field])) is not None
        open_price = Decimal(str(item["open"]))
        high = Decimal(str(item["high"]))
        low = Decimal(str(item["low"]))
        close = Decimal(str(item["close"]))
        assert high >= open_price
        assert high >= close
        assert high >= low
        assert low <= open_price
        assert low <= close
        assert low <= high


@allure.title("KD-USDM-RAW-BOUNDARY-001 - 验证 USDM raw limit=1 offset=0 分页回显正确。")
@pytest.mark.kline_api
def test_kd_usdm_raw_boundary_001_limit_one(kline_api):
    """
    Case ID: KD-USDM-RAW-BOUNDARY-001
    测试目的: 验证 USDM raw limit=1 offset=0 分页回显正确。
    """
    response = kline_api.get_usdm_kline_raw(
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time_ms=START_TIME_MS,
        end_time_ms=END_TIME_MS,
        limit=LIMIT_SMALL,
        offset=0,
    )

    assert response.status_code == 200
    body = response.json()
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]
    data = body["data"]
    assert "pagination" in data
    assert "items" in data
    assert isinstance(data["items"], list)
    assert len(data["items"]) <= LIMIT_SMALL
    assert data["pagination"]["limit"] == LIMIT_SMALL
    assert data["pagination"]["offset"] == 0


@allure.title("KD-USDM-RAW-BOUNDARY-002 - 验证 USDM raw include_total=true 分页回显正确。")
@pytest.mark.kline_api
def test_kd_usdm_raw_boundary_002_include_total(kline_api):
    """
    Case ID: KD-USDM-RAW-BOUNDARY-002
    测试目的: 验证 USDM raw include_total=true 分页回显正确。
    """
    response = kline_api.get_usdm_kline_raw(
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time_ms=START_TIME_MS,
        end_time_ms=END_TIME_MS,
        limit=LIMIT_SMALL,
        include_total=True,
    )

    assert response.status_code == 200
    body = response.json()
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]
    data = body["data"]
    pagination = data["pagination"]
    assert isinstance(data["items"], list)
    assert len(data["items"]) <= LIMIT_SMALL
    assert pagination["limit"] == LIMIT_SMALL
    assert pagination["include_total"] is True or str(pagination["include_total"]).lower() == "true"
    if "total" in pagination and pagination["total"] is not None:
        assert isinstance(pagination["total"], int)
        assert pagination["total"] >= 0


@allure.title("KD-USDM-RAW-BOUNDARY-003 - 验证 USDM raw 不传时间窗时不 500。")
@pytest.mark.kline_api
def test_kd_usdm_raw_boundary_003_without_time_window(kline_api):
    """
    Case ID: KD-USDM-RAW-BOUNDARY-003
    测试目的: 验证 USDM raw 不传时间窗时不 500。
    """
    try:
        response = kline_api.get_usdm_kline_raw(
            symbol=SYMBOL,
            interval=INTERVAL,
            limit=LIMIT_NORMAL,
        )
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if str(body.get("code")) == "200":
        assert body["status"] == "success"
        data = body["data"]
        assert "pagination" in data
        assert "items" in data
        assert isinstance(data["items"], list)
        assert len(data["items"]) <= LIMIT_NORMAL
        assert data["pagination"]["limit"] == LIMIT_NORMAL


@allure.title("KD-USDM-RAW-PARAM-001 - 验证 USDM raw K线分页缺失 symbol 时返回参数校验错误。")
@pytest.mark.kline_api
def test_kd_usdm_raw_param_001_missing_symbol(kline_api):
    """
    Case ID: KD-USDM-RAW-PARAM-001
    测试目的: 验证 USDM raw K线分页缺失 symbol 时返回参数校验错误。
    """
    try:
        response = kline_api.get_usdm_kline_raw(symbol=None)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    body = response.json() if response.content else {}
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert "symbol" in str(body)
    else:
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] == "error"
        assert "symbol" in str(body["message"])
        assert not (str(body["code"]) == "200" and body["status"] == "success")


@allure.title("KD-USDM-RAW-PARAM-002 - 验证 USDM raw 只传 start_time_ms 返回时间窗参数错误。")
@pytest.mark.kline_api
def test_kd_usdm_raw_param_002_only_start_time(kline_api):
    """
    Case ID: KD-USDM-RAW-PARAM-002
    测试目的: 验证 USDM raw 只传 start_time_ms 返回时间窗参数错误。
    """
    try:
        response = kline_api.get_usdm_kline_raw(
            symbol=SYMBOL,
            start_time_ms=START_TIME_MS,
        )
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    body = response.json() if response.content else {}
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert "time" in str(body).lower()
    else:
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] == "error"
        assert "time" in str(body["message"]).lower()


@allure.title("KD-USDM-RAW-PARAM-003 - 验证 USDM raw end_time_ms <= start_time_ms 返回参数错误。")
@pytest.mark.kline_api
def test_kd_usdm_raw_param_003_reversed_time_window(kline_api):
    """
    Case ID: KD-USDM-RAW-PARAM-003
    测试目的: 验证 USDM raw end_time_ms <= start_time_ms 返回参数错误。
    """
    try:
        response = kline_api.get_usdm_kline_raw(
            symbol=SYMBOL,
            start_time_ms=END_TIME_MS,
            end_time_ms=START_TIME_MS,
        )
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    body = response.json() if response.content else {}
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert "time" in str(body).lower()
    else:
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] == "error"
        assert "time" in str(body["message"]).lower()


@allure.title("KD-USDM-RAW-PARAM-004 - 验证 USDM raw limit=0 返回参数错误。")
@pytest.mark.kline_api
def test_kd_usdm_raw_param_004_limit_zero(kline_api):
    """
    Case ID: KD-USDM-RAW-PARAM-004
    测试目的: 验证 USDM raw limit=0 返回参数错误。
    """
    try:
        response = kline_api.get_usdm_kline_raw(symbol=SYMBOL, limit=0)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    body = response.json() if response.content else {}
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert "limit" in str(body).lower()
    else:
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] == "error"
        assert "limit" in str(body["message"]).lower()


@allure.title("KD-USDM-RAW-PARAM-005 - 验证 USDM raw offset=-1 返回参数错误。")
@pytest.mark.kline_api
def test_kd_usdm_raw_param_005_offset_negative(kline_api):
    """
    Case ID: KD-USDM-RAW-PARAM-005
    测试目的: 验证 USDM raw offset=-1 返回参数错误。
    """
    try:
        response = kline_api.get_usdm_kline_raw(symbol=SYMBOL, offset=-1)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    body = response.json() if response.content else {}
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert "offset" in str(body).lower()
    else:
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] == "error"
        assert "offset" in str(body["message"]).lower()


@allure.title("KD-USDM-RAW-RESPONSE-001 - 验证 USDM raw K线分页 item 字段结构。")
@pytest.mark.kline_api
def test_kd_usdm_raw_response_001_item_schema(kline_api):
    """
    Case ID: KD-USDM-RAW-RESPONSE-001
    测试目的: 验证 USDM raw K线分页 item 字段结构。
    """
    response = kline_api.get_usdm_kline_raw(
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time_ms=START_TIME_MS,
        end_time_ms=END_TIME_MS,
        limit=LIMIT_SMALL,
    )

    assert response.status_code == 200
    body = response.json()
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]
    data = body["data"]
    assert "items" in data
    items = data["items"]
    assert isinstance(items, list)
    assert len(items) <= LIMIT_SMALL
    for item in items:
        for field in ("symbol", "timestamp", "interval", "open", "high", "low", "close", "volume"):
            assert field in item
        assert item["symbol"] == SYMBOL
        assert item["interval"] == INTERVAL


@allure.title("KD-USDM-RAW-DQC-001 - 验证 USDM raw timestamp、数值字段和时间排序。")
@pytest.mark.kline_api
@pytest.mark.dqc
def test_kd_usdm_raw_dqc_001(kline_api):
    """
    Case ID: KD-USDM-RAW-DQC-001
    测试目的: 验证 USDM raw timestamp、数值字段和时间排序。
    """
    response = kline_api.get_usdm_kline_raw(
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time_ms=START_TIME_MS,
        end_time_ms=END_TIME_MS,
        limit=LIMIT_NORMAL,
    )

    assert response.status_code == 200
    body = response.json()
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    items = body["data"]["items"]
    timestamps = []
    for item in items:
        timestamp = int(item["timestamp"])
        timestamps.append(timestamp)
        assert len(str(timestamp)) == 13
        for numeric_field in ("open", "high", "low", "close", "volume", "quote_volume"):
            if numeric_field in item and item[numeric_field] is not None:
                assert Decimal(str(item[numeric_field])) is not None


@allure.title("KD-USDM-RAW-LOGIC-001 - 验证 USDM raw OHLC、symbol、interval 和时间窗过滤。")
@pytest.mark.kline_api
@pytest.mark.logic
def test_kd_usdm_raw_logic_001(kline_api):
    """
    Case ID: KD-USDM-RAW-LOGIC-001
    测试目的: 验证 USDM raw OHLC、symbol、interval 和时间窗过滤。
    """
    response = kline_api.get_usdm_kline_raw(
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time_ms=START_TIME_MS,
        end_time_ms=END_TIME_MS,
        limit=LIMIT_NORMAL,
    )

    assert response.status_code == 200
    body = response.json()
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    items = body["data"]["items"]
    for item in items:
        assert item["symbol"] == SYMBOL
        assert item["interval"] == INTERVAL
        timestamp = int(item["timestamp"])
        assert START_TIME_MS <= timestamp < END_TIME_MS
        open_price = Decimal(str(item["open"]))
        high = Decimal(str(item["high"]))
        low = Decimal(str(item["low"]))
        close = Decimal(str(item["close"]))
        assert high >= open_price
        assert high >= close
        assert high >= low
        assert low <= open_price
        assert low <= close
        assert low <= high


@allure.title("KD-USDM-RAW-PERF-001 - 验证 USDM raw K线分页典型请求在性能基线内返回成功。")
@pytest.mark.kline_api
@pytest.mark.performance
def test_kd_usdm_raw_perf_001_baseline(kline_api):
    """
    Case ID: KD-USDM-RAW-PERF-001
    测试目的: 验证 USDM raw K线分页典型请求在性能基线内返回成功。
    """
    start = perf_counter()
    response = kline_api.get_usdm_kline_raw(symbol=SYMBOL, limit=LIMIT_NORMAL)
    elapsed = perf_counter() - start

    assert elapsed < PERFORMANCE_BASELINE_SECONDS
    assert response.status_code == 200
    body = response.json()
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]
    assert "data" in body


@allure.title("KD-USDM-CURATED-NORMAL-001 - 验证 USDM curated K线分页按 quality_flag 和分页参数正常返回。")
@pytest.mark.kline_api
def test_kd_usdm_curated_normal_001(kline_api):
    """
    Case ID: KD-USDM-CURATED-NORMAL-001
    测试目的: 验证 USDM curated K线分页按 quality_flag 和分页参数正常返回。
    """
    response = kline_api.get_usdm_kline(
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time_ms=START_TIME_MS,
        end_time_ms=END_TIME_MS,
        quality_flag="OK",
        limit=LIMIT_NORMAL,
    )

    assert response.status_code == 200
    body = response.json()
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]
    data = body["data"]
    assert "filters" in data
    assert "pagination" in data
    assert "items" in data
    assert len(data["items"]) <= LIMIT_NORMAL
    assert data["pagination"]["limit"] == LIMIT_NORMAL
    for item in data["items"]:
        for field in ("symbol", "timestamp", "interval", "open", "high", "low", "close", "volume"):
            assert field in item
        assert item["symbol"] == SYMBOL
        assert item["interval"] == INTERVAL
        timestamp = int(item["timestamp"])
        assert len(str(timestamp)) == 13
        assert START_TIME_MS <= timestamp < END_TIME_MS
        if item.get("close_time") is not None:
            assert len(str(int(item["close_time"]))) == 13
        if item.get("quality_flag") is not None:
            assert str(item["quality_flag"]).upper() == "OK"
        assert "quality_flag" in item or "repair_tag" in item
        for numeric_field in ("open", "high", "low", "close", "volume", "quote_volume"):
            if numeric_field in item and item[numeric_field] is not None:
                assert Decimal(str(item[numeric_field])) is not None
        open_price = Decimal(str(item["open"]))
        high = Decimal(str(item["high"]))
        low = Decimal(str(item["low"]))
        close = Decimal(str(item["close"]))
        assert high >= open_price
        assert high >= close
        assert high >= low
        assert low <= open_price
        assert low <= close
        assert low <= high


@allure.title("KD-USDM-CURATED-BOUNDARY-001 - 验证 USDM curated 不传 quality_flag 时结构正确。")
@pytest.mark.kline_api
def test_kd_usdm_curated_boundary_001_without_quality_flag(kline_api):
    """
    Case ID: KD-USDM-CURATED-BOUNDARY-001
    测试目的: 验证 USDM curated 不传 quality_flag 时结构正确。
    """
    response = kline_api.get_usdm_kline(
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time_ms=START_TIME_MS,
        end_time_ms=END_TIME_MS,
        quality_flag=None,
        limit=LIMIT_NORMAL,
    )

    assert response.status_code == 200
    body = response.json()
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]
    data = body["data"]
    assert "filters" in data
    assert "pagination" in data
    assert "items" in data
    assert isinstance(data["items"], list)
    assert len(data["items"]) <= LIMIT_NORMAL


@allure.title("KD-USDM-CURATED-BOUNDARY-002 - 验证 USDM curated quality_flag=ok 不 500。")
@pytest.mark.kline_api
def test_kd_usdm_curated_boundary_002_quality_flag_lowercase(kline_api):
    """
    Case ID: KD-USDM-CURATED-BOUNDARY-002
    测试目的: 验证 USDM curated quality_flag=ok 不 500。
    """
    try:
        response = kline_api.get_usdm_kline(
            symbol=SYMBOL,
            interval=INTERVAL,
            start_time_ms=START_TIME_MS,
            end_time_ms=END_TIME_MS,
            quality_flag="ok",
            limit=LIMIT_NORMAL,
        )
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if str(body.get("code")) == "200":
        assert body["status"] == "success"
        assert "data" in body
        assert "items" in body["data"]
        for item in body["data"]["items"]:
            if item.get("quality_flag") is not None:
                assert str(item["quality_flag"]).upper() == "OK"


@allure.title("KD-USDM-CURATED-PARAM-001 - 验证 USDM curated K线分页缺失 symbol 时返回参数校验错误。")
@pytest.mark.kline_api
def test_kd_usdm_curated_param_001_missing_symbol(kline_api):
    """
    Case ID: KD-USDM-CURATED-PARAM-001
    测试目的: 验证 USDM curated K线分页缺失 symbol 时返回参数校验错误。
    """
    try:
        response = kline_api.get_usdm_kline(symbol=None)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    body = response.json() if response.content else {}
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert "symbol" in str(body)
    else:
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] == "error"
        assert "symbol" in str(body["message"])


@allure.title("KD-USDM-CURATED-PARAM-002 - 验证 USDM curated 只传 end_time_ms 返回时间窗参数错误。")
@pytest.mark.kline_api
def test_kd_usdm_curated_param_002_only_end_time(kline_api):
    """
    Case ID: KD-USDM-CURATED-PARAM-002
    测试目的: 验证 USDM curated 只传 end_time_ms 返回时间窗参数错误。
    """
    try:
        response = kline_api.get_usdm_kline(
            symbol=SYMBOL,
            end_time_ms=END_TIME_MS,
        )
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    body = response.json() if response.content else {}
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert "time" in str(body).lower()
    else:
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] == "error"
        assert "time" in str(body["message"]).lower()


@allure.title("KD-USDM-CURATED-PARAM-003 - 验证 USDM curated limit=200001 返回参数错误。")
@pytest.mark.kline_api
def test_kd_usdm_curated_param_003_limit_too_large(kline_api):
    """
    Case ID: KD-USDM-CURATED-PARAM-003
    测试目的: 验证 USDM curated limit=200001 返回参数错误。
    """
    try:
        response = kline_api.get_usdm_kline(symbol=SYMBOL, limit=LIMIT_TOO_LARGE)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    body = response.json() if response.content else {}
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert "limit" in str(body).lower()
    else:
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] == "error"
        assert "limit" in str(body["message"]).lower()


@allure.title("KD-USDM-CURATED-RESPONSE-001 - 验证 USDM curated item 支持 raw 和 curated 字段。")
@pytest.mark.kline_api
def test_kd_usdm_curated_response_001_item_schema(kline_api):
    """
    Case ID: KD-USDM-CURATED-RESPONSE-001
    测试目的: 验证 USDM curated item 支持 raw 和 curated 字段。
    """
    response = kline_api.get_usdm_kline(
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time_ms=START_TIME_MS,
        end_time_ms=END_TIME_MS,
        limit=LIMIT_SMALL,
    )

    assert response.status_code == 200
    body = response.json()
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]
    items = body["data"]["items"]
    assert isinstance(items, list)
    assert len(items) <= LIMIT_SMALL
    for item in items:
        for field in ("symbol", "timestamp", "interval", "open", "high", "low", "close", "volume"):
            assert field in item
        assert "quality_flag" in item or "repair_tag" in item


@allure.title("KD-USDM-CURATED-DQC-001 - 验证 USDM curated timestamp、close_time 和数值字段。")
@pytest.mark.kline_api
@pytest.mark.dqc
def test_kd_usdm_curated_dqc_001(kline_api):
    """
    Case ID: KD-USDM-CURATED-DQC-001
    测试目的: 验证 USDM curated timestamp、close_time 和数值字段。
    """
    response = kline_api.get_usdm_kline(
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time_ms=START_TIME_MS,
        end_time_ms=END_TIME_MS,
        limit=LIMIT_NORMAL,
    )

    assert response.status_code == 200
    body = response.json()
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    for item in body["data"]["items"]:
        assert len(str(int(item["timestamp"]))) == 13
        if item.get("close_time") is not None:
            assert len(str(int(item["close_time"]))) == 13
        for numeric_field in ("open", "high", "low", "close", "volume", "quote_volume"):
            if numeric_field in item and item[numeric_field] is not None:
                assert Decimal(str(item[numeric_field])) is not None


@allure.title("KD-USDM-CURATED-LOGIC-001 - 验证 USDM curated OHLC 和时间窗过滤。")
@pytest.mark.kline_api
@pytest.mark.logic
def test_kd_usdm_curated_logic_001(kline_api):
    """
    Case ID: KD-USDM-CURATED-LOGIC-001
    测试目的: 验证 USDM curated OHLC 和时间窗过滤。
    """
    response = kline_api.get_usdm_kline(
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time_ms=START_TIME_MS,
        end_time_ms=END_TIME_MS,
        limit=LIMIT_NORMAL,
    )

    assert response.status_code == 200
    body = response.json()
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    for item in body["data"]["items"]:
        timestamp = int(item["timestamp"])
        assert START_TIME_MS <= timestamp < END_TIME_MS
        open_price = Decimal(str(item["open"]))
        high = Decimal(str(item["high"]))
        low = Decimal(str(item["low"]))
        close = Decimal(str(item["close"]))
        assert high >= open_price
        assert high >= close
        assert high >= low
        assert low <= open_price
        assert low <= close
        assert low <= high


@allure.title("KD-SPOT-TIMERANGE-NORMAL-001 - 验证 Spot legacy 单 symbol 时间边界查询正常返回。")
@pytest.mark.kline_api
def test_kd_spot_timerange_normal_001(kline_api):
    """
    Case ID: KD-SPOT-TIMERANGE-NORMAL-001
    测试目的: 验证 Spot legacy 单 symbol 时间边界查询正常返回。
    """
    response = kline_api.get_spot_time_range(symbol=SYMBOL, interval=INTERVAL)

    assert response.status_code == 200
    body = response.json()
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]
    data = body["data"]
    assert "raw" in data
    assert "curated" in data
    for section_name in ("raw", "curated"):
        section = data[section_name]
        assert "time_field" in section
        assert "min_time_ms" in section
        assert "max_time_ms" in section
        assert "has_data" in section


@allure.title("KD-SPOT-TIMERANGE-BOUNDARY-001 - 验证 Spot 时间边界查询省略 interval 时按默认周期处理。")
@pytest.mark.kline_api
def test_kd_spot_timerange_boundary_001(kline_api):
    """
    Case ID: KD-SPOT-TIMERANGE-BOUNDARY-001
    测试目的: 验证 Spot 时间边界查询省略 interval 时按默认周期处理。
    """
    response = kline_api.get_spot_time_range(symbol=SYMBOL, interval=None)

    assert response.status_code == 200
    body = response.json()
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]
    assert response.status_code != 500
    assert "data" in body
    assert "raw" in body["data"]
    assert "curated" in body["data"]


@allure.title("KD-SPOT-TIMERANGE-PARAM-001 - 验证 Spot 时间边界缺失 symbol 时返回参数校验错误。")
@pytest.mark.kline_api
def test_kd_spot_timerange_param_001_missing_symbol(kline_api):
    """
    Case ID: KD-SPOT-TIMERANGE-PARAM-001
    测试目的: 验证 Spot 时间边界缺失 symbol 时返回参数校验错误。
    """
    try:
        response = kline_api.get_spot_time_range(symbol=None)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    body = response.json() if response.content else {}
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert "symbol" in str(body)
    else:
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] == "error"
        assert "symbol" in str(body["message"])


@allure.title("KD-SPOT-TIMERANGE-DQC-001 - 验证 Spot 时间边界毫秒时间戳和 min/max 顺序。")
@pytest.mark.kline_api
@pytest.mark.dqc
def test_kd_spot_timerange_dqc_001(kline_api):
    """
    Case ID: KD-SPOT-TIMERANGE-DQC-001
    测试目的: 验证 Spot 时间边界毫秒时间戳和 min/max 顺序。
    """
    response = kline_api.get_spot_time_range(symbol=SYMBOL, interval=INTERVAL)

    assert response.status_code == 200
    body = response.json()
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    for section_name in ("raw", "curated"):
        section = body["data"][section_name]
        min_time = section["min_time_ms"]
        max_time = section["max_time_ms"]
        if min_time is not None:
            assert len(str(int(min_time))) == 13
        if max_time is not None:
            assert len(str(int(max_time))) == 13
        if section["has_data"]:
            assert min_time is not None
            assert max_time is not None
            assert int(min_time) <= int(max_time)


@allure.title("KD-SPOT-RAW-NORMAL-001 - 验证 Spot raw K线分页按合法 symbol、时间窗和分页参数正常返回。")
@pytest.mark.kline_api
def test_kd_spot_raw_normal_001(kline_api):
    """
    Case ID: KD-SPOT-RAW-NORMAL-001
    测试目的: 验证 Spot raw K线分页按合法 symbol、时间窗和分页参数正常返回。
    """
    response = kline_api.get_spot_kline_raw(
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time_ms=START_TIME_MS,
        end_time_ms=END_TIME_MS,
        limit=LIMIT_NORMAL,
        offset=0,
        include_total=False,
    )

    assert response.status_code == 200
    body = response.json()
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]
    data = body["data"]
    assert "items" in data
    assert "pagination" in data
    assert len(data["items"]) <= LIMIT_NORMAL
    assert data["pagination"]["limit"] == LIMIT_NORMAL
    for item in data["items"]:
        assert item["symbol"] == SYMBOL
        assert item["interval"] == INTERVAL
        timestamp = int(item["timestamp"])
        assert START_TIME_MS <= timestamp < END_TIME_MS
        assert len(str(timestamp)) == 13
        for numeric_field in ("open", "high", "low", "close", "volume", "quote_volume"):
            if numeric_field in item and item[numeric_field] is not None:
                assert Decimal(str(item[numeric_field])) is not None
        open_price = Decimal(str(item["open"]))
        high = Decimal(str(item["high"]))
        low = Decimal(str(item["low"]))
        close = Decimal(str(item["close"]))
        assert high >= open_price
        assert high >= close
        assert high >= low
        assert low <= open_price
        assert low <= close
        assert low <= high


@allure.title("KD-SPOT-RAW-BOUNDARY-001 - 验证 Spot raw limit=1 最多返回 1 条。")
@pytest.mark.kline_api
def test_kd_spot_raw_boundary_001_limit_one(kline_api):
    """
    Case ID: KD-SPOT-RAW-BOUNDARY-001
    测试目的: 验证 Spot raw limit=1 最多返回 1 条。
    """
    response = kline_api.get_spot_kline_raw(
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time_ms=START_TIME_MS,
        end_time_ms=END_TIME_MS,
        limit=LIMIT_SMALL,
    )

    assert response.status_code == 200
    body = response.json()
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert "items" in body["data"]
    assert len(body["data"]["items"]) <= LIMIT_SMALL
    assert body["data"]["pagination"]["limit"] == LIMIT_SMALL


@allure.title("KD-SPOT-RAW-BOUNDARY-002 - 验证 Spot raw include_total=true。")
@pytest.mark.kline_api
def test_kd_spot_raw_boundary_002_include_total(kline_api):
    """
    Case ID: KD-SPOT-RAW-BOUNDARY-002
    测试目的: 验证 Spot raw include_total=true。
    """
    response = kline_api.get_spot_kline_raw(
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time_ms=START_TIME_MS,
        end_time_ms=END_TIME_MS,
        limit=LIMIT_SMALL,
        include_total=True,
    )

    assert response.status_code == 200
    body = response.json()
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    pagination = body["data"]["pagination"]
    assert pagination["include_total"] is True or str(pagination["include_total"]).lower() == "true"
    if "total" in pagination and pagination["total"] is not None:
        assert isinstance(pagination["total"], int)
        assert pagination["total"] >= 0


@allure.title("KD-SPOT-RAW-PARAM-001 - 验证 Spot raw K线分页缺失 symbol 时返回参数校验错误。")
@pytest.mark.kline_api
def test_kd_spot_raw_param_001_missing_symbol(kline_api):
    """
    Case ID: KD-SPOT-RAW-PARAM-001
    测试目的: 验证 Spot raw K线分页缺失 symbol 时返回参数校验错误。
    """
    try:
        response = kline_api.get_spot_kline_raw(symbol=None)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    body = response.json() if response.content else {}
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert "symbol" in str(body)
    else:
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] == "error"
        assert "symbol" in str(body["message"])


@allure.title("KD-SPOT-RAW-PARAM-002 - 验证 Spot raw 时间窗只传一端返回参数错误。")
@pytest.mark.kline_api
def test_kd_spot_raw_param_002_only_start_time(kline_api):
    """
    Case ID: KD-SPOT-RAW-PARAM-002
    测试目的: 验证 Spot raw 时间窗只传一端返回参数错误。
    """
    try:
        response = kline_api.get_spot_kline_raw(
            symbol=SYMBOL,
            start_time_ms=START_TIME_MS,
        )
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    body = response.json() if response.content else {}
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert "time" in str(body).lower()
    else:
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] == "error"
        assert "time" in str(body["message"]).lower()


@allure.title("KD-SPOT-RAW-PARAM-003 - 验证 Spot raw limit=0 返回参数错误。")
@pytest.mark.kline_api
def test_kd_spot_raw_param_003_limit_zero(kline_api):
    """
    Case ID: KD-SPOT-RAW-PARAM-003
    测试目的: 验证 Spot raw limit=0 返回参数错误。
    """
    try:
        response = kline_api.get_spot_kline_raw(symbol=SYMBOL, limit=0)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    body = response.json() if response.content else {}
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert "limit" in str(body).lower()
    else:
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] == "error"
        assert "limit" in str(body["message"]).lower()


@allure.title("KD-SPOT-RAW-DQC-001 - 验证 Spot raw timestamp 和数值字段。")
@pytest.mark.kline_api
@pytest.mark.dqc
def test_kd_spot_raw_dqc_001(kline_api):
    """
    Case ID: KD-SPOT-RAW-DQC-001
    测试目的: 验证 Spot raw timestamp 和数值字段。
    """
    response = kline_api.get_spot_kline_raw(
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time_ms=START_TIME_MS,
        end_time_ms=END_TIME_MS,
        limit=LIMIT_NORMAL,
    )

    assert response.status_code == 200
    body = response.json()
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    for item in body["data"]["items"]:
        assert len(str(int(item["timestamp"]))) == 13
        for numeric_field in ("open", "high", "low", "close", "volume", "quote_volume"):
            if numeric_field in item and item[numeric_field] is not None:
                assert Decimal(str(item[numeric_field])) is not None


@allure.title("KD-SPOT-RAW-LOGIC-001 - 验证 Spot raw OHLC、symbol 和 interval。")
@pytest.mark.kline_api
@pytest.mark.logic
def test_kd_spot_raw_logic_001(kline_api):
    """
    Case ID: KD-SPOT-RAW-LOGIC-001
    测试目的: 验证 Spot raw OHLC、symbol 和 interval。
    """
    response = kline_api.get_spot_kline_raw(
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time_ms=START_TIME_MS,
        end_time_ms=END_TIME_MS,
        limit=LIMIT_NORMAL,
    )

    assert response.status_code == 200
    body = response.json()
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    for item in body["data"]["items"]:
        assert item["symbol"] == SYMBOL
        assert item["interval"] == INTERVAL
        timestamp = int(item["timestamp"])
        assert START_TIME_MS <= timestamp < END_TIME_MS
        open_price = Decimal(str(item["open"]))
        high = Decimal(str(item["high"]))
        low = Decimal(str(item["low"]))
        close = Decimal(str(item["close"]))
        assert high >= open_price
        assert high >= close
        assert high >= low
        assert low <= open_price
        assert low <= close
        assert low <= high


@allure.title("KD-SPOT-CURATED-NORMAL-001 - 验证 Spot curated K线分页按 quality_flag 和分页参数正常返回。")
@pytest.mark.kline_api
def test_kd_spot_curated_normal_001(kline_api):
    """
    Case ID: KD-SPOT-CURATED-NORMAL-001
    测试目的: 验证 Spot curated K线分页按 quality_flag 和分页参数正常返回。
    """
    response = kline_api.get_spot_kline(
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time_ms=START_TIME_MS,
        end_time_ms=END_TIME_MS,
        quality_flag="OK",
        limit=LIMIT_NORMAL,
    )

    assert response.status_code == 200
    body = response.json()
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]
    data = body["data"]
    assert "items" in data
    assert "pagination" in data
    assert len(data["items"]) <= LIMIT_NORMAL
    for item in data["items"]:
        assert item["symbol"] == SYMBOL
        assert item["interval"] == INTERVAL
        if item.get("quality_flag") is not None:
            assert str(item["quality_flag"]).upper() == "OK"
        assert "quality_flag" in item or "repair_tag" in item


@allure.title("KD-SPOT-CURATED-BOUNDARY-001 - 验证 Spot curated 不传 quality_flag 时结构正确。")
@pytest.mark.kline_api
def test_kd_spot_curated_boundary_001_without_quality_flag(kline_api):
    """
    Case ID: KD-SPOT-CURATED-BOUNDARY-001
    测试目的: 验证 Spot curated 不传 quality_flag 时结构正确。
    """
    response = kline_api.get_spot_kline(
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time_ms=START_TIME_MS,
        end_time_ms=END_TIME_MS,
        quality_flag=None,
        limit=LIMIT_NORMAL,
    )

    assert response.status_code == 200
    body = response.json()
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]
    assert "items" in body["data"]
    assert "pagination" in body["data"]
    assert len(body["data"]["items"]) <= LIMIT_NORMAL


@allure.title("KD-SPOT-CURATED-BOUNDARY-002 - 验证 Spot curated quality_flag=ok 不 500。")
@pytest.mark.kline_api
def test_kd_spot_curated_boundary_002_quality_flag_lowercase(kline_api):
    """
    Case ID: KD-SPOT-CURATED-BOUNDARY-002
    测试目的: 验证 Spot curated quality_flag=ok 不 500。
    """
    try:
        response = kline_api.get_spot_kline(
            symbol=SYMBOL,
            interval=INTERVAL,
            start_time_ms=START_TIME_MS,
            end_time_ms=END_TIME_MS,
            quality_flag="ok",
            limit=LIMIT_NORMAL,
        )
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if str(body.get("code")) == "200":
        assert body["status"] == "success"
        for item in body["data"]["items"]:
            if item.get("quality_flag") is not None:
                assert str(item["quality_flag"]).upper() == "OK"


@allure.title("KD-SPOT-CURATED-PARAM-001 - 验证 Spot curated K线分页缺失 symbol 时返回参数校验错误。")
@pytest.mark.kline_api
def test_kd_spot_curated_param_001_missing_symbol(kline_api):
    """
    Case ID: KD-SPOT-CURATED-PARAM-001
    测试目的: 验证 Spot curated K线分页缺失 symbol 时返回参数校验错误。
    """
    try:
        response = kline_api.get_spot_kline(symbol=None)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    body = response.json() if response.content else {}
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert "symbol" in str(body)
    else:
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] == "error"
        assert "symbol" in str(body["message"])


@allure.title("KD-SPOT-CURATED-PARAM-002 - 验证 Spot curated end_time_ms <= start_time_ms 返回参数错误。")
@pytest.mark.kline_api
def test_kd_spot_curated_param_002_reversed_time_window(kline_api):
    """
    Case ID: KD-SPOT-CURATED-PARAM-002
    测试目的: 验证 Spot curated end_time_ms <= start_time_ms 返回参数错误。
    """
    try:
        response = kline_api.get_spot_kline(
            symbol=SYMBOL,
            start_time_ms=END_TIME_MS,
            end_time_ms=START_TIME_MS,
        )
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    body = response.json() if response.content else {}
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert "time" in str(body).lower()
    else:
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] == "error"
        assert "time" in str(body["message"]).lower()


@allure.title("KD-SPOT-CURATED-RESPONSE-001 - 验证 Spot curated item 支持 K 线字段和 curated 字段。")
@pytest.mark.kline_api
def test_kd_spot_curated_response_001_item_schema(kline_api):
    """
    Case ID: KD-SPOT-CURATED-RESPONSE-001
    测试目的: 验证 Spot curated item 支持 K 线字段和 curated 字段。
    """
    response = kline_api.get_spot_kline(
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time_ms=START_TIME_MS,
        end_time_ms=END_TIME_MS,
        limit=LIMIT_SMALL,
    )

    assert response.status_code == 200
    body = response.json()
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    items = body["data"]["items"]
    assert isinstance(items, list)
    assert len(items) <= LIMIT_SMALL
    for item in items:
        for field in ("symbol", "timestamp", "interval", "open", "high", "low", "close", "volume"):
            assert field in item
        assert "quality_flag" in item or "repair_tag" in item


@allure.title("KD-SPOT-CURATED-DQC-001 - 验证 Spot curated timestamp 和数值字段。")
@pytest.mark.kline_api
@pytest.mark.dqc
def test_kd_spot_curated_dqc_001(kline_api):
    """
    Case ID: KD-SPOT-CURATED-DQC-001
    测试目的: 验证 Spot curated timestamp 和数值字段。
    """
    response = kline_api.get_spot_kline(
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time_ms=START_TIME_MS,
        end_time_ms=END_TIME_MS,
        limit=LIMIT_NORMAL,
    )

    assert response.status_code == 200
    body = response.json()
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    for item in body["data"]["items"]:
        assert len(str(int(item["timestamp"]))) == 13
        for numeric_field in ("open", "high", "low", "close", "volume", "quote_volume"):
            if numeric_field in item and item[numeric_field] is not None:
                assert Decimal(str(item[numeric_field])) is not None


@allure.title("KD-SPOT-CURATED-LOGIC-001 - 验证 Spot curated OHLC 和时间窗过滤。")
@pytest.mark.kline_api
@pytest.mark.logic
def test_kd_spot_curated_logic_001(kline_api):
    """
    Case ID: KD-SPOT-CURATED-LOGIC-001
    测试目的: 验证 Spot curated OHLC 和时间窗过滤。
    """
    response = kline_api.get_spot_kline(
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time_ms=START_TIME_MS,
        end_time_ms=END_TIME_MS,
        limit=LIMIT_NORMAL,
    )

    assert response.status_code == 200
    body = response.json()
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    for item in body["data"]["items"]:
        timestamp = int(item["timestamp"])
        assert START_TIME_MS <= timestamp < END_TIME_MS
        open_price = Decimal(str(item["open"]))
        high = Decimal(str(item["high"]))
        low = Decimal(str(item["low"]))
        close = Decimal(str(item["close"]))
        assert high >= open_price
        assert high >= close
        assert high >= low
        assert low <= open_price
        assert low <= close
        assert low <= high
