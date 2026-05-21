from __future__ import annotations

from decimal import Decimal
from time import perf_counter

import allure
import pytest
from requests.exceptions import HTTPError

from api.platform.binance_usdm_api import BinanceUSDMAPI
from config.settings import settings

LIMIT_NORMAL = 10
PERFORMANCE_BASELINE_SECONDS = 2.0


@pytest.fixture(scope="module")
def binance_usdm_api() -> BinanceUSDMAPI:
    if not settings.base_url or "exchange.com" in settings.base_url:
        pytest.skip("Binance USDM API BASE_URL is not configured for live API tests.")
    return BinanceUSDMAPI()


@allure.title('BUSDM-VOLUME-NORMAL-001 - Normal - range_unit=hours&n=24&top_k=10&use_quote_volume=true&m_days=7&include_ticker_24h=true')
@allure.feature('binance-usdm')
@allure.story('Normal')
@pytest.mark.binance_usdm_api
def test_busdm_volume_normal_001(binance_usdm_api):
    """
    Case ID: BUSDM-VOLUME-NORMAL-001
    测试大类: binance-usdm
    测试类型: Normal
    测试目的: range_unit=hours&n=24&top_k=10&use_quote_volume=true&m_days=7&include_ticker_24h=true
    预期断言: 成功；`data.now_ms/range_unit/n/top_k/m_days/count/items` 存在
    """

    params = {'range_unit': 'hours', 'n': 24, 'top_k': 10, 'use_quote_volume': True, 'm_days': 7, 'include_ticker_24h': True}

    response = binance_usdm_api.get_volume_rank(**params)

    assert response.status_code == 200
    body = response.json()
    assert "code" in body
    assert "status" in body
    assert "message" in body
    assert "data" in body
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]
    data = body["data"]
    assert data is not None
    assert "now_ms" in data
    assert "range_unit" in data
    assert "n" in data
    assert "top_k" in data
    assert "m_days" in data
    assert "count" in data
    assert "items" in data
    assert len(str(int(data["now_ms"]))) == 13
    assert isinstance(data["items"], list)
    assert data["count"] == len(data["items"])
    if params.get("range_unit") is not None:
        assert data["range_unit"] == params["range_unit"]
    if params.get("top_k") is not None:
        assert len(data["items"]) <= int(params["top_k"])
    volumes = []
    for item in data["items"]:
        assert isinstance(item.get("symbol"), str)
        assert item["symbol"]
        assert item.get("range_unit") == data["range_unit"]
        assert item.get("n") == data["n"]
        if "ticker_as_of" in item and item["ticker_as_of"] is not None:
            assert len(str(int(item["ticker_as_of"]))) == 13
        for numeric_field in ("volume", "quote_volume", "base_volume"):
            if numeric_field in item and item[numeric_field] is not None:
                assert Decimal(str(item[numeric_field])) is not None
        volume_value = item.get("quote_volume") if params.get("use_quote_volume", True) else item.get("volume")
        if volume_value is not None:
            volumes.append(Decimal(str(volume_value)))
        if "history_m_days" in item and item["history_m_days"] is not None:
            assert isinstance(item["history_m_days"], list)
            assert len(item["history_m_days"]) <= int(params.get("m_days", data["m_days"]))
        if params.get("include_ticker_24h") is False:
            assert "ticker_24h" not in item or item["ticker_24h"] in (None, {}, [])


@allure.title('BUSDM-VOLUME-NORMAL-002 - Normal - range_unit=days&n=7&top_k=10')
@allure.feature('binance-usdm')
@allure.story('Normal')
@pytest.mark.binance_usdm_api
def test_busdm_volume_normal_002(binance_usdm_api):
    """
    Case ID: BUSDM-VOLUME-NORMAL-002
    测试大类: binance-usdm
    测试类型: Normal
    测试目的: range_unit=days&n=7&top_k=10
    预期断言: 成功；range_unit 回显为 `days
    """

    params = {'range_unit': 'days', 'n': 7, 'top_k': 10}

    response = binance_usdm_api.get_volume_rank(**params)

    assert response.status_code == 200
    body = response.json()
    assert "code" in body
    assert "status" in body
    assert "message" in body
    assert "data" in body
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]
    data = body["data"]
    assert data is not None
    assert "now_ms" in data
    assert "range_unit" in data
    assert "n" in data
    assert "top_k" in data
    assert "m_days" in data
    assert "count" in data
    assert "items" in data
    assert len(str(int(data["now_ms"]))) == 13
    assert isinstance(data["items"], list)
    assert data["count"] == len(data["items"])
    if params.get("range_unit") is not None:
        assert data["range_unit"] == params["range_unit"]
    if params.get("top_k") is not None:
        assert len(data["items"]) <= int(params["top_k"])
    volumes = []
    for item in data["items"]:
        assert isinstance(item.get("symbol"), str)
        assert item["symbol"]
        assert item.get("range_unit") == data["range_unit"]
        assert item.get("n") == data["n"]
        if "ticker_as_of" in item and item["ticker_as_of"] is not None:
            assert len(str(int(item["ticker_as_of"]))) == 13
        for numeric_field in ("volume", "quote_volume", "base_volume"):
            if numeric_field in item and item[numeric_field] is not None:
                assert Decimal(str(item[numeric_field])) is not None
        volume_value = item.get("quote_volume") if params.get("use_quote_volume", True) else item.get("volume")
        if volume_value is not None:
            volumes.append(Decimal(str(volume_value)))
        if "history_m_days" in item and item["history_m_days"] is not None:
            assert isinstance(item["history_m_days"], list)
            assert len(item["history_m_days"]) <= int(params.get("m_days", data["m_days"]))
        if params.get("include_ticker_24h") is False:
            assert "ticker_24h" not in item or item["ticker_24h"] in (None, {}, [])


@allure.title('BUSDM-VOLUME-BOUNDARY-001 - Boundary - n=1&top_k=1&m_days=1')
@allure.feature('binance-usdm')
@allure.story('Boundary')
@pytest.mark.binance_usdm_api
def test_busdm_volume_boundary_001(binance_usdm_api):
    """
    Case ID: BUSDM-VOLUME-BOUNDARY-001
    测试大类: binance-usdm
    测试类型: Boundary
    测试目的: n=1&top_k=1&m_days=1
    预期断言: 最小边界成功；items 最多 1 条
    """

    params = {'range_unit': 'hours', 'n': 1, 'top_k': 1, 'm_days': 1}

    response = binance_usdm_api.get_volume_rank(**params)

    assert response.status_code == 200
    body = response.json()
    assert "code" in body
    assert "status" in body
    assert "message" in body
    assert "data" in body
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]
    data = body["data"]
    assert data is not None
    assert "now_ms" in data
    assert "range_unit" in data
    assert "n" in data
    assert "top_k" in data
    assert "m_days" in data
    assert "count" in data
    assert "items" in data
    assert len(str(int(data["now_ms"]))) == 13
    assert isinstance(data["items"], list)
    assert data["count"] == len(data["items"])
    if params.get("range_unit") is not None:
        assert data["range_unit"] == params["range_unit"]
    if params.get("top_k") is not None:
        assert len(data["items"]) <= int(params["top_k"])
    volumes = []
    for item in data["items"]:
        assert isinstance(item.get("symbol"), str)
        assert item["symbol"]
        assert item.get("range_unit") == data["range_unit"]
        assert item.get("n") == data["n"]
        if "ticker_as_of" in item and item["ticker_as_of"] is not None:
            assert len(str(int(item["ticker_as_of"]))) == 13
        for numeric_field in ("volume", "quote_volume", "base_volume"):
            if numeric_field in item and item[numeric_field] is not None:
                assert Decimal(str(item[numeric_field])) is not None
        volume_value = item.get("quote_volume") if params.get("use_quote_volume", True) else item.get("volume")
        if volume_value is not None:
            volumes.append(Decimal(str(volume_value)))
        if "history_m_days" in item and item["history_m_days"] is not None:
            assert isinstance(item["history_m_days"], list)
            assert len(item["history_m_days"]) <= int(params.get("m_days", data["m_days"]))
        if params.get("include_ticker_24h") is False:
            assert "ticker_24h" not in item or item["ticker_24h"] in (None, {}, [])


@allure.title('BUSDM-VOLUME-BOUNDARY-002 - Boundary - n=168&top_k=200&m_days=90')
@allure.feature('binance-usdm')
@allure.story('Boundary')
@pytest.mark.binance_usdm_api
def test_busdm_volume_boundary_002(binance_usdm_api):
    """
    Case ID: BUSDM-VOLUME-BOUNDARY-002
    测试大类: binance-usdm
    测试类型: Boundary
    测试目的: n=168&top_k=200&m_days=90
    预期断言: 最大边界成功或在性能上可接受；不能 500
    """

    params = {'range_unit': 'hours', 'n': 168, 'top_k': 200, 'm_days': 90}

    try:
        response = binance_usdm_api.get_volume_rank(**params)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert "code" in body
    assert "status" in body
    assert "message" in body
    assert "data" in body
    assert str(body.get("code", "")) != "500"
    if str(body.get("code")) != "200":
        assert str(body.get("code")) in {"400", "422"} or body.get("status") in {"error", "fail", "failed"}
    else:
        assert body["status"] == "success"
        assert body["message"]
        data = body["data"]
        assert data is not None
        assert "now_ms" in data
        assert "range_unit" in data
        assert "n" in data
        assert "top_k" in data
        assert "m_days" in data
        assert "count" in data
        assert "items" in data
        assert len(str(int(data["now_ms"]))) == 13
        assert isinstance(data["items"], list)
        assert data["count"] == len(data["items"])
        if params.get("range_unit") is not None:
            assert data["range_unit"] == params["range_unit"]
        if params.get("top_k") is not None:
            assert len(data["items"]) <= int(params["top_k"])
        volumes = []
        for item in data["items"]:
            assert isinstance(item.get("symbol"), str)
            assert item["symbol"]
            assert item.get("range_unit") == data["range_unit"]
            assert item.get("n") == data["n"]
            if "ticker_as_of" in item and item["ticker_as_of"] is not None:
                assert len(str(int(item["ticker_as_of"]))) == 13
            for numeric_field in ("volume", "quote_volume", "base_volume"):
                if numeric_field in item and item[numeric_field] is not None:
                    assert Decimal(str(item[numeric_field])) is not None
            volume_value = item.get("quote_volume") if params.get("use_quote_volume", True) else item.get("volume")
            if volume_value is not None:
                volumes.append(Decimal(str(volume_value)))
            if "history_m_days" in item and item["history_m_days"] is not None:
                assert isinstance(item["history_m_days"], list)
                assert len(item["history_m_days"]) <= int(params.get("m_days", data["m_days"]))
            if params.get("include_ticker_24h") is False:
                assert "ticker_24h" not in item or item["ticker_24h"] in (None, {}, [])


@allure.title('BUSDM-VOLUME-BOUNDARY-003 - Boundary - use_quote_volume=false')
@allure.feature('binance-usdm')
@allure.story('Boundary')
@pytest.mark.binance_usdm_api
def test_busdm_volume_boundary_003(binance_usdm_api):
    """
    Case ID: BUSDM-VOLUME-BOUNDARY-003
    测试大类: binance-usdm
    测试类型: Boundary
    测试目的: use_quote_volume=false
    预期断言: 排名按基础资产成交量语义；结构稳定
    """

    params = {'range_unit': 'hours', 'n': 24, 'top_k': 10, 'use_quote_volume': False}

    response = binance_usdm_api.get_volume_rank(**params)

    assert response.status_code == 200
    body = response.json()
    assert "code" in body
    assert "status" in body
    assert "message" in body
    assert "data" in body
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]
    data = body["data"]
    assert data is not None
    assert "now_ms" in data
    assert "range_unit" in data
    assert "n" in data
    assert "top_k" in data
    assert "m_days" in data
    assert "count" in data
    assert "items" in data
    assert len(str(int(data["now_ms"]))) == 13
    assert isinstance(data["items"], list)
    assert data["count"] == len(data["items"])
    if params.get("range_unit") is not None:
        assert data["range_unit"] == params["range_unit"]
    if params.get("top_k") is not None:
        assert len(data["items"]) <= int(params["top_k"])
    volumes = []
    for item in data["items"]:
        assert isinstance(item.get("symbol"), str)
        assert item["symbol"]
        assert item.get("range_unit") == data["range_unit"]
        assert item.get("n") == data["n"]
        if "ticker_as_of" in item and item["ticker_as_of"] is not None:
            assert len(str(int(item["ticker_as_of"]))) == 13
        for numeric_field in ("volume", "quote_volume", "base_volume"):
            if numeric_field in item and item[numeric_field] is not None:
                assert Decimal(str(item[numeric_field])) is not None
        volume_value = item.get("quote_volume") if params.get("use_quote_volume", True) else item.get("volume")
        if volume_value is not None:
            volumes.append(Decimal(str(volume_value)))
        if "history_m_days" in item and item["history_m_days"] is not None:
            assert isinstance(item["history_m_days"], list)
            assert len(item["history_m_days"]) <= int(params.get("m_days", data["m_days"]))
        if params.get("include_ticker_24h") is False:
            assert "ticker_24h" not in item or item["ticker_24h"] in (None, {}, [])


@allure.title('BUSDM-VOLUME-BOUNDARY-004 - Boundary - include_ticker_24h=false')
@allure.feature('binance-usdm')
@allure.story('Boundary')
@pytest.mark.binance_usdm_api
def test_busdm_volume_boundary_004(binance_usdm_api):
    """
    Case ID: BUSDM-VOLUME-BOUNDARY-004
    测试大类: binance-usdm
    测试类型: Boundary
    测试目的: include_ticker_24h=false
    预期断言: item 可不含 `ticker_24h` 或该字段为空；其他字段稳定
    """

    params = {'range_unit': 'hours', 'n': 24, 'top_k': 10, 'include_ticker_24h': False}

    response = binance_usdm_api.get_volume_rank(**params)

    assert response.status_code == 200
    body = response.json()
    assert "code" in body
    assert "status" in body
    assert "message" in body
    assert "data" in body
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]
    data = body["data"]
    assert data is not None
    assert "now_ms" in data
    assert "range_unit" in data
    assert "n" in data
    assert "top_k" in data
    assert "m_days" in data
    assert "count" in data
    assert "items" in data
    assert len(str(int(data["now_ms"]))) == 13
    assert isinstance(data["items"], list)
    assert data["count"] == len(data["items"])
    if params.get("range_unit") is not None:
        assert data["range_unit"] == params["range_unit"]
    if params.get("top_k") is not None:
        assert len(data["items"]) <= int(params["top_k"])
    volumes = []
    for item in data["items"]:
        assert isinstance(item.get("symbol"), str)
        assert item["symbol"]
        assert item.get("range_unit") == data["range_unit"]
        assert item.get("n") == data["n"]
        if "ticker_as_of" in item and item["ticker_as_of"] is not None:
            assert len(str(int(item["ticker_as_of"]))) == 13
        for numeric_field in ("volume", "quote_volume", "base_volume"):
            if numeric_field in item and item[numeric_field] is not None:
                assert Decimal(str(item[numeric_field])) is not None
        volume_value = item.get("quote_volume") if params.get("use_quote_volume", True) else item.get("volume")
        if volume_value is not None:
            volumes.append(Decimal(str(volume_value)))
        if "history_m_days" in item and item["history_m_days"] is not None:
            assert isinstance(item["history_m_days"], list)
            assert len(item["history_m_days"]) <= int(params.get("m_days", data["m_days"]))
        if params.get("include_ticker_24h") is False:
            assert "ticker_24h" not in item or item["ticker_24h"] in (None, {}, [])


@allure.title('BUSDM-VOLUME-PARAM-001 - ParamError - range_unit=weeks')
@allure.feature('binance-usdm')
@allure.story('ParamError')
@pytest.mark.binance_usdm_api
def test_busdm_volume_param_001(binance_usdm_api):
    """
    Case ID: BUSDM-VOLUME-PARAM-001
    测试大类: binance-usdm
    测试类型: ParamError
    测试目的: range_unit=weeks
    预期断言: 返回枚举或业务参数错误
    """

    params = {'range_unit': 'weeks', 'n': 24, 'top_k': 10}

    try:
        response = binance_usdm_api.get_volume_rank(**params)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert any(key in str(body).lower() for key in ('range_unit', 'n', 'top_k', 'm_days', 'limit', 'days_history', 'change_threshold'))
    else:
        assert "code" in body
        assert "status" in body
        assert "message" in body
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] in {"error", "fail", "failed"}
    assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('BUSDM-VOLUME-PARAM-002 - ParamError - n=0')
@allure.feature('binance-usdm')
@allure.story('ParamError')
@pytest.mark.binance_usdm_api
def test_busdm_volume_param_002(binance_usdm_api):
    """
    Case ID: BUSDM-VOLUME-PARAM-002
    测试大类: binance-usdm
    测试类型: ParamError
    测试目的: n=0
    预期断言: 返回参数错误
    """

    params = {'range_unit': 'hours', 'n': 0, 'top_k': 10}

    try:
        response = binance_usdm_api.get_volume_rank(**params)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert any(key in str(body).lower() for key in ('range_unit', 'n', 'top_k', 'm_days', 'limit', 'days_history', 'change_threshold'))
    else:
        assert "code" in body
        assert "status" in body
        assert "message" in body
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] in {"error", "fail", "failed"}
    assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('BUSDM-VOLUME-PARAM-003 - ParamError - n=169')
@allure.feature('binance-usdm')
@allure.story('ParamError')
@pytest.mark.binance_usdm_api
def test_busdm_volume_param_003(binance_usdm_api):
    """
    Case ID: BUSDM-VOLUME-PARAM-003
    测试大类: binance-usdm
    测试类型: ParamError
    测试目的: n=169
    预期断言: 返回参数错误
    """

    params = {'range_unit': 'hours', 'n': 169, 'top_k': 10}

    try:
        response = binance_usdm_api.get_volume_rank(**params)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert any(key in str(body).lower() for key in ('range_unit', 'n', 'top_k', 'm_days', 'limit', 'days_history', 'change_threshold'))
    else:
        assert "code" in body
        assert "status" in body
        assert "message" in body
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] in {"error", "fail", "failed"}
    assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('BUSDM-VOLUME-PARAM-004 - ParamError - top_k=0` 或 `top_k=201')
@allure.feature('binance-usdm')
@allure.story('ParamError')
@pytest.mark.binance_usdm_api
def test_busdm_volume_param_004(binance_usdm_api):
    """
    Case ID: BUSDM-VOLUME-PARAM-004
    测试大类: binance-usdm
    测试类型: ParamError
    测试目的: top_k=0` 或 `top_k=201
    预期断言: 返回参数错误
    """

    params = {'range_unit': 'hours', 'n': 24, 'top_k': 0}

    try:
        response = binance_usdm_api.get_volume_rank(**params)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert any(key in str(body).lower() for key in ('range_unit', 'n', 'top_k', 'm_days', 'limit', 'days_history', 'change_threshold'))
    else:
        assert "code" in body
        assert "status" in body
        assert "message" in body
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] in {"error", "fail", "failed"}
    assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('BUSDM-VOLUME-PARAM-005 - ParamError - m_days=0` 或 `m_days=91')
@allure.feature('binance-usdm')
@allure.story('ParamError')
@pytest.mark.binance_usdm_api
def test_busdm_volume_param_005(binance_usdm_api):
    """
    Case ID: BUSDM-VOLUME-PARAM-005
    测试大类: binance-usdm
    测试类型: ParamError
    测试目的: m_days=0` 或 `m_days=91
    预期断言: 返回参数错误
    """

    params = {'range_unit': 'hours', 'n': 24, 'top_k': 10, 'm_days': 0}

    try:
        response = binance_usdm_api.get_volume_rank(**params)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert any(key in str(body).lower() for key in ('range_unit', 'n', 'top_k', 'm_days', 'limit', 'days_history', 'change_threshold'))
    else:
        assert "code" in body
        assert "status" in body
        assert "message" in body
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] in {"error", "fail", "failed"}
    assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('BUSDM-VOLUME-RESPONSE-001 - Response - 正常请求')
@allure.feature('binance-usdm')
@allure.story('Response')
@pytest.mark.binance_usdm_api
def test_busdm_volume_response_001(binance_usdm_api):
    """
    Case ID: BUSDM-VOLUME-RESPONSE-001
    测试大类: binance-usdm
    测试类型: Response
    测试目的: 正常请求
    预期断言: 每个 item 至少含 `symbol/range_unit/n`，常见含 `volume/ticker_as_of/ticker_24h/history_m_days
    """

    params = {'range_unit': 'hours', 'n': 24, 'top_k': 10, 'm_days': 7}

    response = binance_usdm_api.get_volume_rank(**params)

    assert response.status_code == 200
    body = response.json()
    assert "code" in body
    assert "status" in body
    assert "message" in body
    assert "data" in body
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]
    data = body["data"]
    assert data is not None
    assert "now_ms" in data
    assert "range_unit" in data
    assert "n" in data
    assert "top_k" in data
    assert "m_days" in data
    assert "count" in data
    assert "items" in data
    assert len(str(int(data["now_ms"]))) == 13
    assert isinstance(data["items"], list)
    assert data["count"] == len(data["items"])
    if params.get("range_unit") is not None:
        assert data["range_unit"] == params["range_unit"]
    if params.get("top_k") is not None:
        assert len(data["items"]) <= int(params["top_k"])
    volumes = []
    for item in data["items"]:
        assert isinstance(item.get("symbol"), str)
        assert item["symbol"]
        assert item.get("range_unit") == data["range_unit"]
        assert item.get("n") == data["n"]
        if "ticker_as_of" in item and item["ticker_as_of"] is not None:
            assert len(str(int(item["ticker_as_of"]))) == 13
        for numeric_field in ("volume", "quote_volume", "base_volume"):
            if numeric_field in item and item[numeric_field] is not None:
                assert Decimal(str(item[numeric_field])) is not None
        volume_value = item.get("quote_volume") if params.get("use_quote_volume", True) else item.get("volume")
        if volume_value is not None:
            volumes.append(Decimal(str(volume_value)))
        if "history_m_days" in item and item["history_m_days"] is not None:
            assert isinstance(item["history_m_days"], list)
            assert len(item["history_m_days"]) <= int(params.get("m_days", data["m_days"]))
        if params.get("include_ticker_24h") is False:
            assert "ticker_24h" not in item or item["ticker_24h"] in (None, {}, [])


@allure.title('BUSDM-VOLUME-DQC-001 - DataQuality - 正常请求')
@allure.feature('binance-usdm')
@allure.story('DataQuality')
@pytest.mark.binance_usdm_api
@pytest.mark.dqc
def test_busdm_volume_dqc_001(binance_usdm_api):
    """
    Case ID: BUSDM-VOLUME-DQC-001
    测试大类: binance-usdm
    测试类型: DataQuality
    测试目的: 正常请求
    预期断言: now_ms/ticker_as_of` 为 13 位毫秒；volume 可转数字
    """

    params = {'range_unit': 'hours', 'n': 24, 'top_k': 10, 'm_days': 7}

    response = binance_usdm_api.get_volume_rank(**params)

    assert response.status_code == 200
    body = response.json()
    assert "code" in body
    assert "status" in body
    assert "message" in body
    assert "data" in body
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]
    data = body["data"]
    assert data is not None
    assert "now_ms" in data
    assert "range_unit" in data
    assert "n" in data
    assert "top_k" in data
    assert "m_days" in data
    assert "count" in data
    assert "items" in data
    assert len(str(int(data["now_ms"]))) == 13
    assert isinstance(data["items"], list)
    assert data["count"] == len(data["items"])
    if params.get("range_unit") is not None:
        assert data["range_unit"] == params["range_unit"]
    if params.get("top_k") is not None:
        assert len(data["items"]) <= int(params["top_k"])
    volumes = []
    for item in data["items"]:
        assert isinstance(item.get("symbol"), str)
        assert item["symbol"]
        assert item.get("range_unit") == data["range_unit"]
        assert item.get("n") == data["n"]
        if "ticker_as_of" in item and item["ticker_as_of"] is not None:
            assert len(str(int(item["ticker_as_of"]))) == 13
        for numeric_field in ("volume", "quote_volume", "base_volume"):
            if numeric_field in item and item[numeric_field] is not None:
                assert Decimal(str(item[numeric_field])) is not None
        volume_value = item.get("quote_volume") if params.get("use_quote_volume", True) else item.get("volume")
        if volume_value is not None:
            volumes.append(Decimal(str(volume_value)))
        if "history_m_days" in item and item["history_m_days"] is not None:
            assert isinstance(item["history_m_days"], list)
            assert len(item["history_m_days"]) <= int(params.get("m_days", data["m_days"]))
        if params.get("include_ticker_24h") is False:
            assert "ticker_24h" not in item or item["ticker_24h"] in (None, {}, [])


@allure.title('BUSDM-VOLUME-LOGIC-001 - BusinessLogic - 正常请求')
@allure.feature('binance-usdm')
@allure.story('BusinessLogic')
@pytest.mark.binance_usdm_api
@pytest.mark.logic
def test_busdm_volume_logic_001(binance_usdm_api):
    """
    Case ID: BUSDM-VOLUME-LOGIC-001
    测试大类: binance-usdm
    测试类型: BusinessLogic
    测试目的: 正常请求
    预期断言: items 按 volume 或 quote volume 降序；`history_m_days.length <= m_days
    """

    params = {'range_unit': 'hours', 'n': 24, 'top_k': 10, 'use_quote_volume': True, 'm_days': 7}

    response = binance_usdm_api.get_volume_rank(**params)

    assert response.status_code == 200
    body = response.json()
    assert "code" in body
    assert "status" in body
    assert "message" in body
    assert "data" in body
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]
    data = body["data"]
    assert data is not None
    assert "now_ms" in data
    assert "range_unit" in data
    assert "n" in data
    assert "top_k" in data
    assert "m_days" in data
    assert "count" in data
    assert "items" in data
    assert len(str(int(data["now_ms"]))) == 13
    assert isinstance(data["items"], list)
    assert data["count"] == len(data["items"])
    if params.get("range_unit") is not None:
        assert data["range_unit"] == params["range_unit"]
    if params.get("top_k") is not None:
        assert len(data["items"]) <= int(params["top_k"])
    volumes = []
    for item in data["items"]:
        assert isinstance(item.get("symbol"), str)
        assert item["symbol"]
        assert item.get("range_unit") == data["range_unit"]
        assert item.get("n") == data["n"]
        if "ticker_as_of" in item and item["ticker_as_of"] is not None:
            assert len(str(int(item["ticker_as_of"]))) == 13
        for numeric_field in ("volume", "quote_volume", "base_volume"):
            if numeric_field in item and item[numeric_field] is not None:
                assert Decimal(str(item[numeric_field])) is not None
        volume_value = item.get("quote_volume") if params.get("use_quote_volume", True) else item.get("volume")
        if volume_value is not None:
            volumes.append(Decimal(str(volume_value)))
        if "history_m_days" in item and item["history_m_days"] is not None:
            assert isinstance(item["history_m_days"], list)
            assert len(item["history_m_days"]) <= int(params.get("m_days", data["m_days"]))
        if params.get("include_ticker_24h") is False:
            assert "ticker_24h" not in item or item["ticker_24h"] in (None, {}, [])
    if len(volumes) > 1:
        assert volumes == sorted(volumes, reverse=True)


@allure.title('BUSDM-VOLUME-PERF-001 - Performance - top_k=10&m_days=7')
@allure.feature('binance-usdm')
@allure.story('Performance')
@pytest.mark.binance_usdm_api
@pytest.mark.performance
def test_busdm_volume_perf_001(binance_usdm_api):
    """
    Case ID: BUSDM-VOLUME-PERF-001
    测试大类: binance-usdm
    测试类型: Performance
    测试目的: top_k=10&m_days=7
    预期断言: 响应时间小于 2 秒
    """

    params = {'range_unit': 'hours', 'n': 24, 'top_k': 10, 'm_days': 7}

    start = perf_counter()

    response = binance_usdm_api.get_volume_rank(**params)

    elapsed = perf_counter() - start

    assert response.status_code == 200
    body = response.json()
    assert "code" in body
    assert "status" in body
    assert "message" in body
    assert "data" in body
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]
    data = body["data"]
    assert data is not None
    assert "now_ms" in data
    assert "range_unit" in data
    assert "n" in data
    assert "top_k" in data
    assert "m_days" in data
    assert "count" in data
    assert "items" in data
    assert len(str(int(data["now_ms"]))) == 13
    assert isinstance(data["items"], list)
    assert data["count"] == len(data["items"])
    if params.get("range_unit") is not None:
        assert data["range_unit"] == params["range_unit"]
    if params.get("top_k") is not None:
        assert len(data["items"]) <= int(params["top_k"])
    volumes = []
    for item in data["items"]:
        assert isinstance(item.get("symbol"), str)
        assert item["symbol"]
        assert item.get("range_unit") == data["range_unit"]
        assert item.get("n") == data["n"]
        if "ticker_as_of" in item and item["ticker_as_of"] is not None:
            assert len(str(int(item["ticker_as_of"]))) == 13
        for numeric_field in ("volume", "quote_volume", "base_volume"):
            if numeric_field in item and item[numeric_field] is not None:
                assert Decimal(str(item[numeric_field])) is not None
        volume_value = item.get("quote_volume") if params.get("use_quote_volume", True) else item.get("volume")
        if volume_value is not None:
            volumes.append(Decimal(str(volume_value)))
        if "history_m_days" in item and item["history_m_days"] is not None:
            assert isinstance(item["history_m_days"], list)
            assert len(item["history_m_days"]) <= int(params.get("m_days", data["m_days"]))
        if params.get("include_ticker_24h") is False:
            assert "ticker_24h" not in item or item["ticker_24h"] in (None, {}, [])
    assert elapsed < PERFORMANCE_BASELINE_SECONDS


@allure.title('BUSDM-GAINERS-NORMAL-001 - Normal - change_threshold=5&days_history=10&limit=10')
@allure.feature('binance-usdm')
@allure.story('Normal')
@pytest.mark.binance_usdm_api
def test_busdm_gainers_normal_001(binance_usdm_api):
    """
    Case ID: BUSDM-GAINERS-NORMAL-001
    测试大类: binance-usdm
    测试类型: Normal
    测试目的: change_threshold=5&days_history=10&limit=10
    预期断言: 成功；`data.change_threshold/days_history/limit/count/sort_by/items` 存在
    """

    params = {'change_threshold': 5, 'days_history': 10, 'limit': 10}

    response = binance_usdm_api.get_top_gainers(**params)

    assert response.status_code == 200
    body = response.json()
    assert "code" in body
    assert "status" in body
    assert "message" in body
    assert "data" in body
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]
    data = body["data"]
    assert data is not None
    assert "change_threshold" in data
    assert "days_history" in data
    assert "count" in data
    assert "sort_by" in data
    assert "items" in data
    assert isinstance(data["items"], list)
    assert data["count"] == len(data["items"])
    if params.get("limit") is not None:
        assert len(data["items"]) <= int(params["limit"])
    sort_values = []
    for item in data["items"]:
        assert isinstance(item.get("symbol"), str)
        assert item["symbol"]
        for numeric_field in ("change", "change_percent", "price", "last_price", "volume", "quote_volume"):
            if numeric_field in item and item[numeric_field] is not None:
                assert Decimal(str(item[numeric_field])) is not None
        if data["sort_by"] in item and item[data["sort_by"]] is not None:
            sort_values.append(Decimal(str(item[data["sort_by"]])))
        threshold_field = item.get("change_percent", item.get("change"))
        if threshold_field is not None and params.get("change_threshold") is not None:
            assert Decimal(str(threshold_field)) >= Decimal(str(params["change_threshold"])) or params["change_threshold"] == 1000


@allure.title('BUSDM-GAINERS-BOUNDARY-001 - Boundary - change_threshold=0&days_history=1&limit=1')
@allure.feature('binance-usdm')
@allure.story('Boundary')
@pytest.mark.binance_usdm_api
def test_busdm_gainers_boundary_001(binance_usdm_api):
    """
    Case ID: BUSDM-GAINERS-BOUNDARY-001
    测试大类: binance-usdm
    测试类型: Boundary
    测试目的: change_threshold=0&days_history=1&limit=1
    预期断言: 最小边界成功；items 最多 1 条
    """

    params = {'change_threshold': 0, 'days_history': 1, 'limit': 1}

    response = binance_usdm_api.get_top_gainers(**params)

    assert response.status_code == 200
    body = response.json()
    assert "code" in body
    assert "status" in body
    assert "message" in body
    assert "data" in body
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]
    data = body["data"]
    assert data is not None
    assert "change_threshold" in data
    assert "days_history" in data
    assert "count" in data
    assert "sort_by" in data
    assert "items" in data
    assert isinstance(data["items"], list)
    assert data["count"] == len(data["items"])
    if params.get("limit") is not None:
        assert len(data["items"]) <= int(params["limit"])
    sort_values = []
    for item in data["items"]:
        assert isinstance(item.get("symbol"), str)
        assert item["symbol"]
        for numeric_field in ("change", "change_percent", "price", "last_price", "volume", "quote_volume"):
            if numeric_field in item and item[numeric_field] is not None:
                assert Decimal(str(item[numeric_field])) is not None
        if data["sort_by"] in item and item[data["sort_by"]] is not None:
            sort_values.append(Decimal(str(item[data["sort_by"]])))
        threshold_field = item.get("change_percent", item.get("change"))
        if threshold_field is not None and params.get("change_threshold") is not None:
            assert Decimal(str(threshold_field)) >= Decimal(str(params["change_threshold"])) or params["change_threshold"] == 1000


@allure.title('BUSDM-GAINERS-BOUNDARY-002 - Boundary - days_history=60')
@allure.feature('binance-usdm')
@allure.story('Boundary')
@pytest.mark.binance_usdm_api
def test_busdm_gainers_boundary_002(binance_usdm_api):
    """
    Case ID: BUSDM-GAINERS-BOUNDARY-002
    测试大类: binance-usdm
    测试类型: Boundary
    测试目的: days_history=60
    预期断言: 最大历史天数成功或返回明确业务提示；不能 500
    """

    params = {'change_threshold': 5, 'days_history': 60, 'limit': 10}

    try:
        response = binance_usdm_api.get_top_gainers(**params)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert "code" in body
    assert "status" in body
    assert "message" in body
    assert "data" in body
    assert str(body.get("code", "")) != "500"
    if str(body.get("code")) != "200":
        assert str(body.get("code")) in {"400", "422"} or body.get("status") in {"error", "fail", "failed"}
    else:
        assert body["status"] == "success"
        assert body["message"]
        data = body["data"]
        assert data is not None
        assert "change_threshold" in data
        assert "days_history" in data
        assert "count" in data
        assert "sort_by" in data
        assert "items" in data
        assert isinstance(data["items"], list)
        assert data["count"] == len(data["items"])
        if params.get("limit") is not None:
            assert len(data["items"]) <= int(params["limit"])
        sort_values = []
        for item in data["items"]:
            assert isinstance(item.get("symbol"), str)
            assert item["symbol"]
            for numeric_field in ("change", "change_percent", "price", "last_price", "volume", "quote_volume"):
                if numeric_field in item and item[numeric_field] is not None:
                    assert Decimal(str(item[numeric_field])) is not None
            if data["sort_by"] in item and item[data["sort_by"]] is not None:
                sort_values.append(Decimal(str(item[data["sort_by"]])))
            threshold_field = item.get("change_percent", item.get("change"))
            if threshold_field is not None and params.get("change_threshold") is not None:
                assert Decimal(str(threshold_field)) >= Decimal(str(params["change_threshold"])) or params["change_threshold"] == 1000


@allure.title('BUSDM-GAINERS-BOUNDARY-003 - Boundary - 不传 `limit')
@allure.feature('binance-usdm')
@allure.story('Boundary')
@pytest.mark.binance_usdm_api
def test_busdm_gainers_boundary_003(binance_usdm_api):
    """
    Case ID: BUSDM-GAINERS-BOUNDARY-003
    测试大类: binance-usdm
    测试类型: Boundary
    测试目的: 不传 `limit
    预期断言: 返回全量或默认范围；结构稳定
    """

    params = {'change_threshold': 5, 'days_history': 10, 'limit': None}

    response = binance_usdm_api.get_top_gainers(**params)

    assert response.status_code == 200
    body = response.json()
    assert "code" in body
    assert "status" in body
    assert "message" in body
    assert "data" in body
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]
    data = body["data"]
    assert data is not None
    assert "change_threshold" in data
    assert "days_history" in data
    assert "count" in data
    assert "sort_by" in data
    assert "items" in data
    assert isinstance(data["items"], list)
    assert data["count"] == len(data["items"])
    if params.get("limit") is not None:
        assert len(data["items"]) <= int(params["limit"])
    sort_values = []
    for item in data["items"]:
        assert isinstance(item.get("symbol"), str)
        assert item["symbol"]
        for numeric_field in ("change", "change_percent", "price", "last_price", "volume", "quote_volume"):
            if numeric_field in item and item[numeric_field] is not None:
                assert Decimal(str(item[numeric_field])) is not None
        if data["sort_by"] in item and item[data["sort_by"]] is not None:
            sort_values.append(Decimal(str(item[data["sort_by"]])))
        threshold_field = item.get("change_percent", item.get("change"))
        if threshold_field is not None and params.get("change_threshold") is not None:
            assert Decimal(str(threshold_field)) >= Decimal(str(params["change_threshold"])) or params["change_threshold"] == 1000


@allure.title('BUSDM-GAINERS-BOUNDARY-004 - Boundary - change_threshold=1000')
@allure.feature('binance-usdm')
@allure.story('Boundary')
@pytest.mark.binance_usdm_api
def test_busdm_gainers_boundary_004(binance_usdm_api):
    """
    Case ID: BUSDM-GAINERS-BOUNDARY-004
    测试大类: binance-usdm
    测试类型: Boundary
    测试目的: change_threshold=1000
    预期断言: 可返回空数组；不能 500
    """

    params = {'change_threshold': 1000, 'days_history': 10, 'limit': 10}

    try:
        response = binance_usdm_api.get_top_gainers(**params)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert "code" in body
    assert "status" in body
    assert "message" in body
    assert "data" in body
    assert str(body.get("code", "")) != "500"
    if str(body.get("code")) != "200":
        assert str(body.get("code")) in {"400", "422"} or body.get("status") in {"error", "fail", "failed"}
    else:
        assert body["status"] == "success"
        assert body["message"]
        data = body["data"]
        assert data is not None
        assert "change_threshold" in data
        assert "days_history" in data
        assert "count" in data
        assert "sort_by" in data
        assert "items" in data
        assert isinstance(data["items"], list)
        assert data["count"] == len(data["items"])
        if params.get("limit") is not None:
            assert len(data["items"]) <= int(params["limit"])
        sort_values = []
        for item in data["items"]:
            assert isinstance(item.get("symbol"), str)
            assert item["symbol"]
            for numeric_field in ("change", "change_percent", "price", "last_price", "volume", "quote_volume"):
                if numeric_field in item and item[numeric_field] is not None:
                    assert Decimal(str(item[numeric_field])) is not None
            if data["sort_by"] in item and item[data["sort_by"]] is not None:
                sort_values.append(Decimal(str(item[data["sort_by"]])))
            threshold_field = item.get("change_percent", item.get("change"))
            if threshold_field is not None and params.get("change_threshold") is not None:
                assert Decimal(str(threshold_field)) >= Decimal(str(params["change_threshold"])) or params["change_threshold"] == 1000


@allure.title('BUSDM-GAINERS-PARAM-001 - ParamError - days_history=0')
@allure.feature('binance-usdm')
@allure.story('ParamError')
@pytest.mark.binance_usdm_api
def test_busdm_gainers_param_001(binance_usdm_api):
    """
    Case ID: BUSDM-GAINERS-PARAM-001
    测试大类: binance-usdm
    测试类型: ParamError
    测试目的: days_history=0
    预期断言: 返回参数错误
    """

    params = {'change_threshold': 5, 'days_history': 0, 'limit': 10}

    try:
        response = binance_usdm_api.get_top_gainers(**params)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert any(key in str(body).lower() for key in ('range_unit', 'n', 'top_k', 'm_days', 'limit', 'days_history', 'change_threshold'))
    else:
        assert "code" in body
        assert "status" in body
        assert "message" in body
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] in {"error", "fail", "failed"}
    assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('BUSDM-GAINERS-PARAM-002 - ParamError - days_history=61')
@allure.feature('binance-usdm')
@allure.story('ParamError')
@pytest.mark.binance_usdm_api
def test_busdm_gainers_param_002(binance_usdm_api):
    """
    Case ID: BUSDM-GAINERS-PARAM-002
    测试大类: binance-usdm
    测试类型: ParamError
    测试目的: days_history=61
    预期断言: 返回参数错误
    """

    params = {'change_threshold': 5, 'days_history': 61, 'limit': 10}

    try:
        response = binance_usdm_api.get_top_gainers(**params)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert any(key in str(body).lower() for key in ('range_unit', 'n', 'top_k', 'm_days', 'limit', 'days_history', 'change_threshold'))
    else:
        assert "code" in body
        assert "status" in body
        assert "message" in body
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] in {"error", "fail", "failed"}
    assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('BUSDM-GAINERS-PARAM-003 - ParamError - limit=0')
@allure.feature('binance-usdm')
@allure.story('ParamError')
@pytest.mark.binance_usdm_api
def test_busdm_gainers_param_003(binance_usdm_api):
    """
    Case ID: BUSDM-GAINERS-PARAM-003
    测试大类: binance-usdm
    测试类型: ParamError
    测试目的: limit=0
    预期断言: 返回参数错误
    """

    params = {'change_threshold': 5, 'days_history': 10, 'limit': 0}

    try:
        response = binance_usdm_api.get_top_gainers(**params)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert any(key in str(body).lower() for key in ('range_unit', 'n', 'top_k', 'm_days', 'limit', 'days_history', 'change_threshold'))
    else:
        assert "code" in body
        assert "status" in body
        assert "message" in body
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] in {"error", "fail", "failed"}
    assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('BUSDM-GAINERS-PARAM-004 - ParamError - change_threshold=not_number')
@allure.feature('binance-usdm')
@allure.story('ParamError')
@pytest.mark.binance_usdm_api
def test_busdm_gainers_param_004(binance_usdm_api):
    """
    Case ID: BUSDM-GAINERS-PARAM-004
    测试大类: binance-usdm
    测试类型: ParamError
    测试目的: change_threshold=not_number
    预期断言: 返回参数类型错误
    """

    params = {'change_threshold': 'not_number', 'days_history': 10, 'limit': 10}

    try:
        response = binance_usdm_api.get_top_gainers(**params)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert any(key in str(body).lower() for key in ('range_unit', 'n', 'top_k', 'm_days', 'limit', 'days_history', 'change_threshold'))
    else:
        assert "code" in body
        assert "status" in body
        assert "message" in body
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] in {"error", "fail", "failed"}
    assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('BUSDM-GAINERS-RESPONSE-001 - Response - 正常请求')
@allure.feature('binance-usdm')
@allure.story('Response')
@pytest.mark.binance_usdm_api
def test_busdm_gainers_response_001(binance_usdm_api):
    """
    Case ID: BUSDM-GAINERS-RESPONSE-001
    测试大类: binance-usdm
    测试类型: Response
    测试目的: 正常请求
    预期断言: item 存在时至少含 `symbol`；返回的涨幅、价格、成交量和历史字段必须类型合法
    """

    params = {'change_threshold': 5, 'days_history': 10, 'limit': 10}

    response = binance_usdm_api.get_top_gainers(**params)

    assert response.status_code == 200
    body = response.json()
    assert "code" in body
    assert "status" in body
    assert "message" in body
    assert "data" in body
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]
    data = body["data"]
    assert data is not None
    assert "change_threshold" in data
    assert "days_history" in data
    assert "count" in data
    assert "sort_by" in data
    assert "items" in data
    assert isinstance(data["items"], list)
    assert data["count"] == len(data["items"])
    if params.get("limit") is not None:
        assert len(data["items"]) <= int(params["limit"])
    sort_values = []
    for item in data["items"]:
        assert isinstance(item.get("symbol"), str)
        assert item["symbol"]
        for numeric_field in ("change", "change_percent", "price", "last_price", "volume", "quote_volume"):
            if numeric_field in item and item[numeric_field] is not None:
                assert Decimal(str(item[numeric_field])) is not None
        if data["sort_by"] in item and item[data["sort_by"]] is not None:
            sort_values.append(Decimal(str(item[data["sort_by"]])))
        threshold_field = item.get("change_percent", item.get("change"))
        if threshold_field is not None and params.get("change_threshold") is not None:
            assert Decimal(str(threshold_field)) >= Decimal(str(params["change_threshold"])) or params["change_threshold"] == 1000


@allure.title('BUSDM-GAINERS-DQC-001 - DataQuality - 正常请求')
@allure.feature('binance-usdm')
@allure.story('DataQuality')
@pytest.mark.binance_usdm_api
@pytest.mark.dqc
def test_busdm_gainers_dqc_001(binance_usdm_api):
    """
    Case ID: BUSDM-GAINERS-DQC-001
    测试大类: binance-usdm
    测试类型: DataQuality
    测试目的: 正常请求
    预期断言: 涨跌幅、价格、成交量等字段存在时可转数字
    """

    params = {'change_threshold': 5, 'days_history': 10, 'limit': 10}

    response = binance_usdm_api.get_top_gainers(**params)

    assert response.status_code == 200
    body = response.json()
    assert "code" in body
    assert "status" in body
    assert "message" in body
    assert "data" in body
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]
    data = body["data"]
    assert data is not None
    assert "change_threshold" in data
    assert "days_history" in data
    assert "count" in data
    assert "sort_by" in data
    assert "items" in data
    assert isinstance(data["items"], list)
    assert data["count"] == len(data["items"])
    if params.get("limit") is not None:
        assert len(data["items"]) <= int(params["limit"])
    sort_values = []
    for item in data["items"]:
        assert isinstance(item.get("symbol"), str)
        assert item["symbol"]
        for numeric_field in ("change", "change_percent", "price", "last_price", "volume", "quote_volume"):
            if numeric_field in item and item[numeric_field] is not None:
                assert Decimal(str(item[numeric_field])) is not None
        if data["sort_by"] in item and item[data["sort_by"]] is not None:
            sort_values.append(Decimal(str(item[data["sort_by"]])))
        threshold_field = item.get("change_percent", item.get("change"))
        if threshold_field is not None and params.get("change_threshold") is not None:
            assert Decimal(str(threshold_field)) >= Decimal(str(params["change_threshold"])) or params["change_threshold"] == 1000


@allure.title('BUSDM-GAINERS-LOGIC-001 - BusinessLogic - 正常请求')
@allure.feature('binance-usdm')
@allure.story('BusinessLogic')
@pytest.mark.binance_usdm_api
@pytest.mark.logic
def test_busdm_gainers_logic_001(binance_usdm_api):
    """
    Case ID: BUSDM-GAINERS-LOGIC-001
    测试大类: binance-usdm
    测试类型: BusinessLogic
    测试目的: 正常请求
    预期断言: items 按 `sort_by` 指定字段排序；涨幅不低于 `change_threshold`，除非字段缺失时按接口实际行为记录
    """

    params = {'change_threshold': 5, 'days_history': 10, 'limit': 10}

    response = binance_usdm_api.get_top_gainers(**params)

    assert response.status_code == 200
    body = response.json()
    assert "code" in body
    assert "status" in body
    assert "message" in body
    assert "data" in body
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]
    data = body["data"]
    assert data is not None
    assert "change_threshold" in data
    assert "days_history" in data
    assert "count" in data
    assert "sort_by" in data
    assert "items" in data
    assert isinstance(data["items"], list)
    assert data["count"] == len(data["items"])
    if params.get("limit") is not None:
        assert len(data["items"]) <= int(params["limit"])
    sort_values = []
    for item in data["items"]:
        assert isinstance(item.get("symbol"), str)
        assert item["symbol"]
        for numeric_field in ("change", "change_percent", "price", "last_price", "volume", "quote_volume"):
            if numeric_field in item and item[numeric_field] is not None:
                assert Decimal(str(item[numeric_field])) is not None
        if data["sort_by"] in item and item[data["sort_by"]] is not None:
            sort_values.append(Decimal(str(item[data["sort_by"]])))
        threshold_field = item.get("change_percent", item.get("change"))
        if threshold_field is not None and params.get("change_threshold") is not None:
            assert Decimal(str(threshold_field)) >= Decimal(str(params["change_threshold"])) or params["change_threshold"] == 1000
    if len(sort_values) > 1:
        assert sort_values == sorted(sort_values, reverse=True)


@allure.title('BUSDM-GAINERS-PERF-001 - Performance - limit=10&days_history=10')
@allure.feature('binance-usdm')
@allure.story('Performance')
@pytest.mark.binance_usdm_api
@pytest.mark.performance
def test_busdm_gainers_perf_001(binance_usdm_api):
    """
    Case ID: BUSDM-GAINERS-PERF-001
    测试大类: binance-usdm
    测试类型: Performance
    测试目的: limit=10&days_history=10
    预期断言: 响应时间小于 2 秒
    """

    params = {'change_threshold': 5, 'days_history': 10, 'limit': 10}

    start = perf_counter()

    response = binance_usdm_api.get_top_gainers(**params)

    elapsed = perf_counter() - start

    assert response.status_code == 200
    body = response.json()
    assert "code" in body
    assert "status" in body
    assert "message" in body
    assert "data" in body
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]
    data = body["data"]
    assert data is not None
    assert "change_threshold" in data
    assert "days_history" in data
    assert "count" in data
    assert "sort_by" in data
    assert "items" in data
    assert isinstance(data["items"], list)
    assert data["count"] == len(data["items"])
    if params.get("limit") is not None:
        assert len(data["items"]) <= int(params["limit"])
    sort_values = []
    for item in data["items"]:
        assert isinstance(item.get("symbol"), str)
        assert item["symbol"]
        for numeric_field in ("change", "change_percent", "price", "last_price", "volume", "quote_volume"):
            if numeric_field in item and item[numeric_field] is not None:
                assert Decimal(str(item[numeric_field])) is not None
        if data["sort_by"] in item and item[data["sort_by"]] is not None:
            sort_values.append(Decimal(str(item[data["sort_by"]])))
        threshold_field = item.get("change_percent", item.get("change"))
        if threshold_field is not None and params.get("change_threshold") is not None:
            assert Decimal(str(threshold_field)) >= Decimal(str(params["change_threshold"])) or params["change_threshold"] == 1000
    assert elapsed < PERFORMANCE_BASELINE_SECONDS

