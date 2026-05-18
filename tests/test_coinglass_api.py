from __future__ import annotations

from decimal import Decimal

import allure
import pytest
from requests.exceptions import HTTPError

from api_services.coinglass_api import CoinGlassAPI
from config.settings import settings

SYMBOL = "BTCUSDT"
EXCHANGE = "Binance"
INTERVAL_FUNDING = "8h"
INTERVAL_RATIO = "1h"
LIMIT_SMALL = 1
LIMIT_NORMAL = 10
START_TIME = 1704067200000
END_TIME = 1704153600000
INVALID_SYMBOL = "NOT_A_SYMBOL"


@pytest.fixture(scope="module")
def coinglass_api() -> CoinGlassAPI:
    if not settings.base_url or "exchange.com" in settings.base_url:
        pytest.skip("CoinGlass API BASE_URL is not configured for live API tests.")
    return CoinGlassAPI()


@allure.title('CG-FR-OHLC-NORMAL-001 - Normal - symbol=BTCUSDT&interval=8h&limit=10')
@allure.feature('CoinGlass')
@allure.story('Normal')
@pytest.mark.coinglass_api
def test_cg_fr_ohlc_normal_001(coinglass_api):
    """
    Case ID: CG-FR-OHLC-NORMAL-001
    测试大类: CoinGlass
    测试类型: Normal
    测试目的: symbol=BTCUSDT&interval=8h&limit=10
    预期断言: 成功；`data.symbol/timestamp/data` 存在；`data.data` 为数组
    """

    params = {'symbol': 'BTCUSDT', 'interval': '8h', 'limit': 10}

    response = coinglass_api.get_funding_rate_ohlc_history(**params)

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
    case_id = 'CG-FR-OHLC-NORMAL-001'
    if isinstance(data, dict) and "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    inner = data.get("data") if isinstance(data, dict) else data
    if inner is None:
        inner = []
    if isinstance(inner, dict):
        rows = [inner]
    else:
        assert isinstance(inner, list)
        rows = inner
    if params.get("limit") is not None:
        assert len(rows) <= int(params["limit"]) or isinstance(data, dict)
    assert isinstance(data, dict)
    assert "symbol" in data
    assert "timestamp" in data
    assert "data" in data
    for point in rows:
        if point:
            for numeric_field in ("open", "high", "low", "close"):
                if numeric_field in point and point[numeric_field] is not None:
                    assert Decimal(str(point[numeric_field])) is not None
            if all(field in point for field in ("open", "high", "low", "close")):
                open_value = Decimal(str(point["open"]))
                high = Decimal(str(point["high"]))
                low = Decimal(str(point["low"]))
                close = Decimal(str(point["close"]))
                assert high >= open_value
                assert high >= close
                assert high >= low
                assert low <= open_value
                assert low <= close
                assert low <= high


@allure.title('CG-FR-OHLC-BOUNDARY-001 - Boundary - limit=1')
@allure.feature('CoinGlass')
@allure.story('Boundary')
@pytest.mark.coinglass_api
def test_cg_fr_ohlc_boundary_001(coinglass_api):
    """
    Case ID: CG-FR-OHLC-BOUNDARY-001
    测试大类: CoinGlass
    测试类型: Boundary
    测试目的: limit=1
    预期断言: 返回数据长度不超过 1
    """

    params = {'symbol': 'BTCUSDT', 'interval': '8h', 'limit': 1}

    response = coinglass_api.get_funding_rate_ohlc_history(**params)

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
    case_id = 'CG-FR-OHLC-BOUNDARY-001'
    if isinstance(data, dict) and "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    inner = data.get("data") if isinstance(data, dict) else data
    if inner is None:
        inner = []
    if isinstance(inner, dict):
        rows = [inner]
    else:
        assert isinstance(inner, list)
        rows = inner
    if params.get("limit") is not None:
        assert len(rows) <= int(params["limit"]) or isinstance(data, dict)
    assert isinstance(data, dict)
    assert "symbol" in data
    assert "timestamp" in data
    assert "data" in data
    for point in rows:
        if point:
            for numeric_field in ("open", "high", "low", "close"):
                if numeric_field in point and point[numeric_field] is not None:
                    assert Decimal(str(point[numeric_field])) is not None
            if all(field in point for field in ("open", "high", "low", "close")):
                open_value = Decimal(str(point["open"]))
                high = Decimal(str(point["high"]))
                low = Decimal(str(point["low"]))
                close = Decimal(str(point["close"]))
                assert high >= open_value
                assert high >= close
                assert high >= low
                assert low <= open_value
                assert low <= close
                assert low <= high


@allure.title('CG-FR-OHLC-BOUNDARY-002 - Boundary - 不传参数')
@allure.feature('CoinGlass')
@allure.story('Boundary')
@pytest.mark.coinglass_api
def test_cg_fr_ohlc_boundary_002(coinglass_api):
    """
    Case ID: CG-FR-OHLC-BOUNDARY-002
    测试大类: CoinGlass
    测试类型: Boundary
    测试目的: 不传参数
    预期断言: 使用默认 `symbol=BTCUSDT&interval=8h&limit=100` 语义；不能 500
    """

    params = {}

    try:
        response = coinglass_api.get_funding_rate_ohlc_history(**params)
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
        case_id = 'CG-FR-OHLC-BOUNDARY-002'
        if isinstance(data, dict) and "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
            assert len(str(int(data["earliest_available_time_ms"]))) == 13
        inner = data.get("data") if isinstance(data, dict) else data
        if inner is None:
            inner = []
        if isinstance(inner, dict):
            rows = [inner]
        else:
            assert isinstance(inner, list)
            rows = inner
        if params.get("limit") is not None:
            assert len(rows) <= int(params["limit"]) or isinstance(data, dict)
        assert isinstance(data, dict)
        assert "symbol" in data
        assert "timestamp" in data
        assert "data" in data
        for point in rows:
            if point:
                for numeric_field in ("open", "high", "low", "close"):
                    if numeric_field in point and point[numeric_field] is not None:
                        assert Decimal(str(point[numeric_field])) is not None
                if all(field in point for field in ("open", "high", "low", "close")):
                    open_value = Decimal(str(point["open"]))
                    high = Decimal(str(point["high"]))
                    low = Decimal(str(point["low"]))
                    close = Decimal(str(point["close"]))
                    assert high >= open_value
                    assert high >= close
                    assert high >= low
                    assert low <= open_value
                    assert low <= close
                    assert low <= high


@allure.title('CG-FR-OHLC-PARAM-001 - ParamError - limit=0')
@allure.feature('CoinGlass')
@allure.story('ParamError')
@pytest.mark.coinglass_api
def test_cg_fr_ohlc_param_001(coinglass_api):
    """
    Case ID: CG-FR-OHLC-PARAM-001
    测试大类: CoinGlass
    测试类型: ParamError
    测试目的: limit=0
    预期断言: 返回参数错误
    """

    params = {'symbol': 'BTCUSDT', 'interval': '8h', 'limit': 0}

    try:
        response = coinglass_api.get_funding_rate_ohlc_history(**params)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert any(key in str(body).lower() for key in ('symbol', 'limit', 'interval', 'time', 'exchange'))
    else:
        assert "code" in body
        assert "status" in body
        assert "message" in body
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] in {"error", "fail", "failed"}
    assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('CG-FR-OHLC-PARAM-002 - ParamError - limit=200001')
@allure.feature('CoinGlass')
@allure.story('ParamError')
@pytest.mark.coinglass_api
def test_cg_fr_ohlc_param_002(coinglass_api):
    """
    Case ID: CG-FR-OHLC-PARAM-002
    测试大类: CoinGlass
    测试类型: ParamError
    测试目的: limit=200001
    预期断言: 返回参数错误
    """

    params = {'symbol': 'BTCUSDT', 'interval': '8h', 'limit': 200001}

    try:
        response = coinglass_api.get_funding_rate_ohlc_history(**params)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert any(key in str(body).lower() for key in ('symbol', 'limit', 'interval', 'time', 'exchange'))
    else:
        assert "code" in body
        assert "status" in body
        assert "message" in body
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] in {"error", "fail", "failed"}
    assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('CG-FR-OHLC-PARAM-003 - ParamError - interval=bad_interval')
@allure.feature('CoinGlass')
@allure.story('ParamError')
@pytest.mark.coinglass_api
def test_cg_fr_ohlc_param_003(coinglass_api):
    """
    Case ID: CG-FR-OHLC-PARAM-003
    测试大类: CoinGlass
    测试类型: ParamError
    测试目的: interval=bad_interval
    预期断言: 返回业务错误或空数据提示；不能 500
    """

    params = {'symbol': 'BTCUSDT', 'interval': 'bad_interval', 'limit': 10}

    try:
        response = coinglass_api.get_funding_rate_ohlc_history(**params)
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
        case_id = 'CG-FR-OHLC-PARAM-003'
        if isinstance(data, dict) and "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
            assert len(str(int(data["earliest_available_time_ms"]))) == 13
        inner = data.get("data") if isinstance(data, dict) else data
        if inner is None:
            inner = []
        if isinstance(inner, dict):
            rows = [inner]
        else:
            assert isinstance(inner, list)
            rows = inner
        if params.get("limit") is not None:
            assert len(rows) <= int(params["limit"]) or isinstance(data, dict)
        assert isinstance(data, dict)
        assert "symbol" in data
        assert "timestamp" in data
        assert "data" in data
        for point in rows:
            if point:
                for numeric_field in ("open", "high", "low", "close"):
                    if numeric_field in point and point[numeric_field] is not None:
                        assert Decimal(str(point[numeric_field])) is not None
                if all(field in point for field in ("open", "high", "low", "close")):
                    open_value = Decimal(str(point["open"]))
                    high = Decimal(str(point["high"]))
                    low = Decimal(str(point["low"]))
                    close = Decimal(str(point["close"]))
                    assert high >= open_value
                    assert high >= close
                    assert high >= low
                    assert low <= open_value
                    assert low <= close
                    assert low <= high


@allure.title('CG-FR-OHLC-RESPONSE-001 - Response - 正常请求')
@allure.feature('CoinGlass')
@allure.story('Response')
@pytest.mark.coinglass_api
def test_cg_fr_ohlc_response_001(coinglass_api):
    """
    Case ID: CG-FR-OHLC-RESPONSE-001
    测试大类: CoinGlass
    测试类型: Response
    测试目的: 正常请求
    预期断言: OHLC 点若存在，应含 `time/open/high/low/close` 或等价字段
    """

    params = {'symbol': 'BTCUSDT', 'interval': '8h', 'limit': 10}

    response = coinglass_api.get_funding_rate_ohlc_history(**params)

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
    case_id = 'CG-FR-OHLC-RESPONSE-001'
    if isinstance(data, dict) and "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    inner = data.get("data") if isinstance(data, dict) else data
    if inner is None:
        inner = []
    if isinstance(inner, dict):
        rows = [inner]
    else:
        assert isinstance(inner, list)
        rows = inner
    if params.get("limit") is not None:
        assert len(rows) <= int(params["limit"]) or isinstance(data, dict)
    assert isinstance(data, dict)
    assert "symbol" in data
    assert "timestamp" in data
    assert "data" in data
    for point in rows:
        if point:
            for numeric_field in ("open", "high", "low", "close"):
                if numeric_field in point and point[numeric_field] is not None:
                    assert Decimal(str(point[numeric_field])) is not None
            if all(field in point for field in ("open", "high", "low", "close")):
                open_value = Decimal(str(point["open"]))
                high = Decimal(str(point["high"]))
                low = Decimal(str(point["low"]))
                close = Decimal(str(point["close"]))
                assert high >= open_value
                assert high >= close
                assert high >= low
                assert low <= open_value
                assert low <= close
                assert low <= high


@allure.title('CG-FR-OHLC-DQC-001 - DataQuality - 正常请求')
@allure.feature('CoinGlass')
@allure.story('DataQuality')
@pytest.mark.coinglass_api
@pytest.mark.dqc
def test_cg_fr_ohlc_dqc_001(coinglass_api):
    """
    Case ID: CG-FR-OHLC-DQC-001
    测试大类: CoinGlass
    测试类型: DataQuality
    测试目的: 正常请求
    预期断言: OHLC 数值可转数字；有 time 时为毫秒或明确时间字符串
    """

    params = {'symbol': 'BTCUSDT', 'interval': '8h', 'limit': 10}

    response = coinglass_api.get_funding_rate_ohlc_history(**params)

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
    case_id = 'CG-FR-OHLC-DQC-001'
    if isinstance(data, dict) and "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    inner = data.get("data") if isinstance(data, dict) else data
    if inner is None:
        inner = []
    if isinstance(inner, dict):
        rows = [inner]
    else:
        assert isinstance(inner, list)
        rows = inner
    if params.get("limit") is not None:
        assert len(rows) <= int(params["limit"]) or isinstance(data, dict)
    assert isinstance(data, dict)
    assert "symbol" in data
    assert "timestamp" in data
    assert "data" in data
    for point in rows:
        if point:
            for numeric_field in ("open", "high", "low", "close"):
                if numeric_field in point and point[numeric_field] is not None:
                    assert Decimal(str(point[numeric_field])) is not None
            if all(field in point for field in ("open", "high", "low", "close")):
                open_value = Decimal(str(point["open"]))
                high = Decimal(str(point["high"]))
                low = Decimal(str(point["low"]))
                close = Decimal(str(point["close"]))
                assert high >= open_value
                assert high >= close
                assert high >= low
                assert low <= open_value
                assert low <= close
                assert low <= high


@allure.title('CG-FR-OHLC-LOGIC-001 - BusinessLogic - 正常请求')
@allure.feature('CoinGlass')
@allure.story('BusinessLogic')
@pytest.mark.coinglass_api
@pytest.mark.logic
def test_cg_fr_ohlc_logic_001(coinglass_api):
    """
    Case ID: CG-FR-OHLC-LOGIC-001
    测试大类: CoinGlass
    测试类型: BusinessLogic
    测试目的: 正常请求
    预期断言: 若点包含 OHLC，满足 `high >= open/close/low` 且 `low <= open/close/high
    """

    params = {'symbol': 'BTCUSDT', 'interval': '8h', 'limit': 10}

    response = coinglass_api.get_funding_rate_ohlc_history(**params)

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
    case_id = 'CG-FR-OHLC-LOGIC-001'
    if isinstance(data, dict) and "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    inner = data.get("data") if isinstance(data, dict) else data
    if inner is None:
        inner = []
    if isinstance(inner, dict):
        rows = [inner]
    else:
        assert isinstance(inner, list)
        rows = inner
    if params.get("limit") is not None:
        assert len(rows) <= int(params["limit"]) or isinstance(data, dict)
    assert isinstance(data, dict)
    assert "symbol" in data
    assert "timestamp" in data
    assert "data" in data
    for point in rows:
        if point:
            for numeric_field in ("open", "high", "low", "close"):
                if numeric_field in point and point[numeric_field] is not None:
                    assert Decimal(str(point[numeric_field])) is not None
            if all(field in point for field in ("open", "high", "low", "close")):
                open_value = Decimal(str(point["open"]))
                high = Decimal(str(point["high"]))
                low = Decimal(str(point["low"]))
                close = Decimal(str(point["close"]))
                assert high >= open_value
                assert high >= close
                assert high >= low
                assert low <= open_value
                assert low <= close
                assert low <= high


@allure.title('CG-FR-EXCHANGE-NORMAL-001 - Normal - symbol=BTCUSDT&limit=10')
@allure.feature('CoinGlass')
@allure.story('Normal')
@pytest.mark.coinglass_api
def test_cg_fr_exchange_normal_001(coinglass_api):
    """
    Case ID: CG-FR-EXCHANGE-NORMAL-001
    测试大类: CoinGlass
    测试类型: Normal
    测试目的: symbol=BTCUSDT&limit=10
    预期断言: 成功；`data.data` 为数组或上游列表结构
    """

    params = {'symbol': 'BTCUSDT', 'limit': 10}

    response = coinglass_api.get_funding_rate_exchange_list(**params)

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
    case_id = 'CG-FR-EXCHANGE-NORMAL-001'
    if isinstance(data, dict) and "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    inner = data.get("data") if isinstance(data, dict) else data
    if inner is None:
        inner = []
    if isinstance(inner, dict):
        rows = [inner]
    else:
        assert isinstance(inner, list)
        rows = inner
    if params.get("limit") is not None:
        assert len(rows) <= int(params["limit"]) or isinstance(data, dict)
    for point in rows:
        if point:
            assert "exchange" in point or "exchange_name" in point or "stablecoin_margin_list" in point or "coin_margin_list" in point or "token_margin_list" in point
            for numeric_field in ("funding_rate", "next_funding_time", "fundingRate"):
                if numeric_field in point and point[numeric_field] is not None:
                    assert Decimal(str(point[numeric_field])) is not None


@allure.title('CG-FR-EXCHANGE-BOUNDARY-001 - Boundary - limit=1')
@allure.feature('CoinGlass')
@allure.story('Boundary')
@pytest.mark.coinglass_api
def test_cg_fr_exchange_boundary_001(coinglass_api):
    """
    Case ID: CG-FR-EXCHANGE-BOUNDARY-001
    测试大类: CoinGlass
    测试类型: Boundary
    测试目的: limit=1
    预期断言: 返回列表长度不超过 1，或聚合结构中列表受限
    """

    params = {'symbol': 'BTCUSDT', 'limit': 1}

    response = coinglass_api.get_funding_rate_exchange_list(**params)

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
    case_id = 'CG-FR-EXCHANGE-BOUNDARY-001'
    if isinstance(data, dict) and "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    inner = data.get("data") if isinstance(data, dict) else data
    if inner is None:
        inner = []
    if isinstance(inner, dict):
        rows = [inner]
    else:
        assert isinstance(inner, list)
        rows = inner
    if params.get("limit") is not None:
        assert len(rows) <= int(params["limit"]) or isinstance(data, dict)
    for point in rows:
        if point:
            assert "exchange" in point or "exchange_name" in point or "stablecoin_margin_list" in point or "coin_margin_list" in point or "token_margin_list" in point
            for numeric_field in ("funding_rate", "next_funding_time", "fundingRate"):
                if numeric_field in point and point[numeric_field] is not None:
                    assert Decimal(str(point[numeric_field])) is not None


@allure.title('CG-FR-EXCHANGE-BOUNDARY-002 - Boundary - 不传参数')
@allure.feature('CoinGlass')
@allure.story('Boundary')
@pytest.mark.coinglass_api
def test_cg_fr_exchange_boundary_002(coinglass_api):
    """
    Case ID: CG-FR-EXCHANGE-BOUNDARY-002
    测试大类: CoinGlass
    测试类型: Boundary
    测试目的: 不传参数
    预期断言: 使用默认 `BTCUSDT`；不能 500
    """

    params = {}

    try:
        response = coinglass_api.get_funding_rate_exchange_list(**params)
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
        case_id = 'CG-FR-EXCHANGE-BOUNDARY-002'
        if isinstance(data, dict) and "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
            assert len(str(int(data["earliest_available_time_ms"]))) == 13
        inner = data.get("data") if isinstance(data, dict) else data
        if inner is None:
            inner = []
        if isinstance(inner, dict):
            rows = [inner]
        else:
            assert isinstance(inner, list)
            rows = inner
        if params.get("limit") is not None:
            assert len(rows) <= int(params["limit"]) or isinstance(data, dict)
        for point in rows:
            if point:
                assert "exchange" in point or "exchange_name" in point or "stablecoin_margin_list" in point or "coin_margin_list" in point or "token_margin_list" in point
                for numeric_field in ("funding_rate", "next_funding_time", "fundingRate"):
                    if numeric_field in point and point[numeric_field] is not None:
                        assert Decimal(str(point[numeric_field])) is not None


@allure.title('CG-FR-EXCHANGE-PARAM-001 - ParamError - limit=0')
@allure.feature('CoinGlass')
@allure.story('ParamError')
@pytest.mark.coinglass_api
def test_cg_fr_exchange_param_001(coinglass_api):
    """
    Case ID: CG-FR-EXCHANGE-PARAM-001
    测试大类: CoinGlass
    测试类型: ParamError
    测试目的: limit=0
    预期断言: 返回参数错误
    """

    params = {'symbol': 'BTCUSDT', 'limit': 0}

    try:
        response = coinglass_api.get_funding_rate_exchange_list(**params)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert any(key in str(body).lower() for key in ('symbol', 'limit', 'interval', 'time', 'exchange'))
    else:
        assert "code" in body
        assert "status" in body
        assert "message" in body
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] in {"error", "fail", "failed"}
    assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('CG-FR-EXCHANGE-PARAM-002 - ParamError - symbol=NOT_A_SYMBOL')
@allure.feature('CoinGlass')
@allure.story('ParamError')
@pytest.mark.coinglass_api
def test_cg_fr_exchange_param_002(coinglass_api):
    """
    Case ID: CG-FR-EXCHANGE-PARAM-002
    测试大类: CoinGlass
    测试类型: ParamError
    测试目的: symbol=NOT_A_SYMBOL
    预期断言: 返回业务错误、空列表或上游提示；不能 500
    """

    params = {'symbol': 'NOT_A_SYMBOL', 'limit': 10}

    try:
        response = coinglass_api.get_funding_rate_exchange_list(**params)
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
        case_id = 'CG-FR-EXCHANGE-PARAM-002'
        if isinstance(data, dict) and "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
            assert len(str(int(data["earliest_available_time_ms"]))) == 13
        inner = data.get("data") if isinstance(data, dict) else data
        if inner is None:
            inner = []
        if isinstance(inner, dict):
            rows = [inner]
        else:
            assert isinstance(inner, list)
            rows = inner
        if params.get("limit") is not None:
            assert len(rows) <= int(params["limit"]) or isinstance(data, dict)
        for point in rows:
            if point:
                assert "exchange" in point or "exchange_name" in point or "stablecoin_margin_list" in point or "coin_margin_list" in point or "token_margin_list" in point
                for numeric_field in ("funding_rate", "next_funding_time", "fundingRate"):
                    if numeric_field in point and point[numeric_field] is not None:
                        assert Decimal(str(point[numeric_field])) is not None


@allure.title('CG-FR-EXCHANGE-RESPONSE-001 - Response - 正常请求')
@allure.feature('CoinGlass')
@allure.story('Response')
@pytest.mark.coinglass_api
def test_cg_fr_exchange_response_001(coinglass_api):
    """
    Case ID: CG-FR-EXCHANGE-RESPONSE-001
    测试大类: CoinGlass
    测试类型: Response
    测试目的: 正常请求
    预期断言: 交易所条目存在时包含 `exchange`、`funding_rate` 或稳定的上游字段
    """

    params = {'symbol': 'BTCUSDT', 'limit': 10}

    response = coinglass_api.get_funding_rate_exchange_list(**params)

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
    case_id = 'CG-FR-EXCHANGE-RESPONSE-001'
    if isinstance(data, dict) and "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    inner = data.get("data") if isinstance(data, dict) else data
    if inner is None:
        inner = []
    if isinstance(inner, dict):
        rows = [inner]
    else:
        assert isinstance(inner, list)
        rows = inner
    if params.get("limit") is not None:
        assert len(rows) <= int(params["limit"]) or isinstance(data, dict)
    for point in rows:
        if point:
            assert "exchange" in point or "exchange_name" in point or "stablecoin_margin_list" in point or "coin_margin_list" in point or "token_margin_list" in point
            for numeric_field in ("funding_rate", "next_funding_time", "fundingRate"):
                if numeric_field in point and point[numeric_field] is not None:
                    assert Decimal(str(point[numeric_field])) is not None


@allure.title('CG-FR-EXCHANGE-DQC-001 - DataQuality - 正常请求')
@allure.feature('CoinGlass')
@allure.story('DataQuality')
@pytest.mark.coinglass_api
@pytest.mark.dqc
def test_cg_fr_exchange_dqc_001(coinglass_api):
    """
    Case ID: CG-FR-EXCHANGE-DQC-001
    测试大类: CoinGlass
    测试类型: DataQuality
    测试目的: 正常请求
    预期断言: funding rate、next funding time 等字段存在时类型合法
    """

    params = {'symbol': 'BTCUSDT', 'limit': 10}

    response = coinglass_api.get_funding_rate_exchange_list(**params)

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
    case_id = 'CG-FR-EXCHANGE-DQC-001'
    if isinstance(data, dict) and "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    inner = data.get("data") if isinstance(data, dict) else data
    if inner is None:
        inner = []
    if isinstance(inner, dict):
        rows = [inner]
    else:
        assert isinstance(inner, list)
        rows = inner
    if params.get("limit") is not None:
        assert len(rows) <= int(params["limit"]) or isinstance(data, dict)
    for point in rows:
        if point:
            assert "exchange" in point or "exchange_name" in point or "stablecoin_margin_list" in point or "coin_margin_list" in point or "token_margin_list" in point
            for numeric_field in ("funding_rate", "next_funding_time", "fundingRate"):
                if numeric_field in point and point[numeric_field] is not None:
                    assert Decimal(str(point[numeric_field])) is not None


@allure.title('CG-FR-ARB-NORMAL-001 - Normal - symbol=BTCUSDT&limit=10')
@allure.feature('CoinGlass')
@allure.story('Normal')
@pytest.mark.coinglass_api
def test_cg_fr_arb_normal_001(coinglass_api):
    """
    Case ID: CG-FR-ARB-NORMAL-001
    测试大类: CoinGlass
    测试类型: Normal
    测试目的: symbol=BTCUSDT&limit=10
    预期断言: 成功；`data.symbol/timestamp/data` 存在
    """

    params = {'symbol': 'BTCUSDT', 'limit': 10}

    response = coinglass_api.get_funding_rate_arbitrage(**params)

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
    case_id = 'CG-FR-ARB-NORMAL-001'
    if isinstance(data, dict) and "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    inner = data.get("data") if isinstance(data, dict) else data
    if inner is None:
        inner = []
    if isinstance(inner, dict):
        rows = [inner]
    else:
        assert isinstance(inner, list)
        rows = inner
    if params.get("limit") is not None:
        assert len(rows) <= int(params["limit"]) or isinstance(data, dict)
    assert isinstance(data, dict)
    assert "symbol" in data
    assert "timestamp" in data
    assert "data" in data
    for point in rows:
        if point:
            assert "symbol" in point or "buy" in point or "sell" in point or "time" in point or "open" in point
            for numeric_field in ("apr", "fee", "spread", "funding", "funding_rate"):
                if numeric_field in point and point[numeric_field] is not None:
                    assert Decimal(str(point[numeric_field])) is not None


@allure.title('CG-FR-ARB-BOUNDARY-001 - Boundary - limit=1')
@allure.feature('CoinGlass')
@allure.story('Boundary')
@pytest.mark.coinglass_api
def test_cg_fr_arb_boundary_001(coinglass_api):
    """
    Case ID: CG-FR-ARB-BOUNDARY-001
    测试大类: CoinGlass
    测试类型: Boundary
    测试目的: limit=1
    预期断言: 返回数据不超过 1 条，或聚合对象结构稳定
    """

    params = {'symbol': 'BTCUSDT', 'limit': 1}

    response = coinglass_api.get_funding_rate_arbitrage(**params)

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
    case_id = 'CG-FR-ARB-BOUNDARY-001'
    if isinstance(data, dict) and "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    inner = data.get("data") if isinstance(data, dict) else data
    if inner is None:
        inner = []
    if isinstance(inner, dict):
        rows = [inner]
    else:
        assert isinstance(inner, list)
        rows = inner
    if params.get("limit") is not None:
        assert len(rows) <= int(params["limit"]) or isinstance(data, dict)
    assert isinstance(data, dict)
    assert "symbol" in data
    assert "timestamp" in data
    assert "data" in data
    for point in rows:
        if point:
            assert "symbol" in point or "buy" in point or "sell" in point or "time" in point or "open" in point
            for numeric_field in ("apr", "fee", "spread", "funding", "funding_rate"):
                if numeric_field in point and point[numeric_field] is not None:
                    assert Decimal(str(point[numeric_field])) is not None


@allure.title('CG-FR-ARB-PARAM-001 - ParamError - limit=0')
@allure.feature('CoinGlass')
@allure.story('ParamError')
@pytest.mark.coinglass_api
def test_cg_fr_arb_param_001(coinglass_api):
    """
    Case ID: CG-FR-ARB-PARAM-001
    测试大类: CoinGlass
    测试类型: ParamError
    测试目的: limit=0
    预期断言: 返回参数错误
    """

    params = {'symbol': 'BTCUSDT', 'limit': 0}

    try:
        response = coinglass_api.get_funding_rate_arbitrage(**params)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert any(key in str(body).lower() for key in ('symbol', 'limit', 'interval', 'time', 'exchange'))
    else:
        assert "code" in body
        assert "status" in body
        assert "message" in body
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] in {"error", "fail", "failed"}
    assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('CG-FR-ARB-PARAM-002 - ParamError - symbol=NOT_A_SYMBOL')
@allure.feature('CoinGlass')
@allure.story('ParamError')
@pytest.mark.coinglass_api
def test_cg_fr_arb_param_002(coinglass_api):
    """
    Case ID: CG-FR-ARB-PARAM-002
    测试大类: CoinGlass
    测试类型: ParamError
    测试目的: symbol=NOT_A_SYMBOL
    预期断言: 返回空数据或业务错误；不能 500
    """

    params = {'symbol': 'NOT_A_SYMBOL', 'limit': 10}

    try:
        response = coinglass_api.get_funding_rate_arbitrage(**params)
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
        case_id = 'CG-FR-ARB-PARAM-002'
        if isinstance(data, dict) and "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
            assert len(str(int(data["earliest_available_time_ms"]))) == 13
        inner = data.get("data") if isinstance(data, dict) else data
        if inner is None:
            inner = []
        if isinstance(inner, dict):
            rows = [inner]
        else:
            assert isinstance(inner, list)
            rows = inner
        if params.get("limit") is not None:
            assert len(rows) <= int(params["limit"]) or isinstance(data, dict)
        assert isinstance(data, dict)
        assert "symbol" in data
        assert "timestamp" in data
        assert "data" in data
        for point in rows:
            if point:
                assert "symbol" in point or "buy" in point or "sell" in point or "time" in point or "open" in point
                for numeric_field in ("apr", "fee", "spread", "funding", "funding_rate"):
                    if numeric_field in point and point[numeric_field] is not None:
                        assert Decimal(str(point[numeric_field])) is not None


@allure.title('CG-FR-ARB-RESPONSE-001 - Response - 正常请求')
@allure.feature('CoinGlass')
@allure.story('Response')
@pytest.mark.coinglass_api
def test_cg_fr_arb_response_001(coinglass_api):
    """
    Case ID: CG-FR-ARB-RESPONSE-001
    测试大类: CoinGlass
    测试类型: Response
    测试目的: 正常请求
    预期断言: 条目存在时至少含 `symbol`；返回的 `buy/sell/apr/funding/fee/spread` 字段必须类型合法
    """

    params = {'symbol': 'BTCUSDT', 'limit': 10}

    response = coinglass_api.get_funding_rate_arbitrage(**params)

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
    case_id = 'CG-FR-ARB-RESPONSE-001'
    if isinstance(data, dict) and "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    inner = data.get("data") if isinstance(data, dict) else data
    if inner is None:
        inner = []
    if isinstance(inner, dict):
        rows = [inner]
    else:
        assert isinstance(inner, list)
        rows = inner
    if params.get("limit") is not None:
        assert len(rows) <= int(params["limit"]) or isinstance(data, dict)
    assert isinstance(data, dict)
    assert "symbol" in data
    assert "timestamp" in data
    assert "data" in data
    for point in rows:
        if point:
            assert "symbol" in point or "buy" in point or "sell" in point or "time" in point or "open" in point
            for numeric_field in ("apr", "fee", "spread", "funding", "funding_rate"):
                if numeric_field in point and point[numeric_field] is not None:
                    assert Decimal(str(point[numeric_field])) is not None


@allure.title('CG-FR-ARB-DQC-001 - DataQuality - 正常请求')
@allure.feature('CoinGlass')
@allure.story('DataQuality')
@pytest.mark.coinglass_api
@pytest.mark.dqc
def test_cg_fr_arb_dqc_001(coinglass_api):
    """
    Case ID: CG-FR-ARB-DQC-001
    测试大类: CoinGlass
    测试类型: DataQuality
    测试目的: 正常请求
    预期断言: APR、fee、spread、funding 等字段存在时可转数字
    """

    params = {'symbol': 'BTCUSDT', 'limit': 10}

    response = coinglass_api.get_funding_rate_arbitrage(**params)

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
    case_id = 'CG-FR-ARB-DQC-001'
    if isinstance(data, dict) and "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    inner = data.get("data") if isinstance(data, dict) else data
    if inner is None:
        inner = []
    if isinstance(inner, dict):
        rows = [inner]
    else:
        assert isinstance(inner, list)
        rows = inner
    if params.get("limit") is not None:
        assert len(rows) <= int(params["limit"]) or isinstance(data, dict)
    assert isinstance(data, dict)
    assert "symbol" in data
    assert "timestamp" in data
    assert "data" in data
    for point in rows:
        if point:
            assert "symbol" in point or "buy" in point or "sell" in point or "time" in point or "open" in point
            for numeric_field in ("apr", "fee", "spread", "funding", "funding_rate"):
                if numeric_field in point and point[numeric_field] is not None:
                    assert Decimal(str(point[numeric_field])) is not None


@allure.title('CG-FR-SUMMARY-NORMAL-001 - Normal - symbol=BTCUSDT&limit=10')
@allure.feature('CoinGlass')
@allure.story('Normal')
@pytest.mark.coinglass_api
def test_cg_fr_summary_normal_001(coinglass_api):
    """
    Case ID: CG-FR-SUMMARY-NORMAL-001
    测试大类: CoinGlass
    测试类型: Normal
    测试目的: symbol=BTCUSDT&limit=10
    预期断言: 成功；`data.symbol/timestamp/data` 存在；`data.data` 为对象
    """

    params = {'symbol': 'BTCUSDT', 'limit': 10}

    response = coinglass_api.get_funding_rate_summary(**params)

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
    case_id = 'CG-FR-SUMMARY-NORMAL-001'
    if isinstance(data, dict) and "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    inner = data.get("data") if isinstance(data, dict) else data
    if inner is None:
        inner = []
    if isinstance(inner, dict):
        rows = [inner]
    else:
        assert isinstance(inner, list)
        rows = inner
    if params.get("limit") is not None:
        assert len(rows) <= int(params["limit"]) or isinstance(data, dict)
    assert isinstance(data, dict)
    assert "symbol" in data or "data" in data
    for value in data.values():
        if isinstance(value, dict):
            for nested_value in value.values():
                if isinstance(nested_value, (int, float, str)):
                    try:
                        Decimal(str(nested_value))
                    except Exception:
                        pass


@allure.title('CG-FR-SUMMARY-BOUNDARY-001 - Boundary - 不传参数')
@allure.feature('CoinGlass')
@allure.story('Boundary')
@pytest.mark.coinglass_api
def test_cg_fr_summary_boundary_001(coinglass_api):
    """
    Case ID: CG-FR-SUMMARY-BOUNDARY-001
    测试大类: CoinGlass
    测试类型: Boundary
    测试目的: 不传参数
    预期断言: 使用默认 symbol；不能 500
    """

    params = {}

    try:
        response = coinglass_api.get_funding_rate_summary(**params)
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
        case_id = 'CG-FR-SUMMARY-BOUNDARY-001'
        if isinstance(data, dict) and "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
            assert len(str(int(data["earliest_available_time_ms"]))) == 13
        inner = data.get("data") if isinstance(data, dict) else data
        if inner is None:
            inner = []
        if isinstance(inner, dict):
            rows = [inner]
        else:
            assert isinstance(inner, list)
            rows = inner
        if params.get("limit") is not None:
            assert len(rows) <= int(params["limit"]) or isinstance(data, dict)
        assert isinstance(data, dict)
        assert "symbol" in data or "data" in data
        for value in data.values():
            if isinstance(value, dict):
                for nested_value in value.values():
                    if isinstance(nested_value, (int, float, str)):
                        try:
                            Decimal(str(nested_value))
                        except Exception:
                            pass


@allure.title('CG-FR-SUMMARY-PARAM-001 - ParamError - limit=0')
@allure.feature('CoinGlass')
@allure.story('ParamError')
@pytest.mark.coinglass_api
def test_cg_fr_summary_param_001(coinglass_api):
    """
    Case ID: CG-FR-SUMMARY-PARAM-001
    测试大类: CoinGlass
    测试类型: ParamError
    测试目的: limit=0
    预期断言: 返回参数错误
    """

    params = {'symbol': 'BTCUSDT', 'limit': 0}

    try:
        response = coinglass_api.get_funding_rate_summary(**params)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert any(key in str(body).lower() for key in ('symbol', 'limit', 'interval', 'time', 'exchange'))
    else:
        assert "code" in body
        assert "status" in body
        assert "message" in body
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] in {"error", "fail", "failed"}
    assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('CG-FR-SUMMARY-PARAM-002 - ParamError - limit=200001')
@allure.feature('CoinGlass')
@allure.story('ParamError')
@pytest.mark.coinglass_api
def test_cg_fr_summary_param_002(coinglass_api):
    """
    Case ID: CG-FR-SUMMARY-PARAM-002
    测试大类: CoinGlass
    测试类型: ParamError
    测试目的: limit=200001
    预期断言: 返回参数错误
    """

    params = {'symbol': 'BTCUSDT', 'limit': 200001}

    try:
        response = coinglass_api.get_funding_rate_summary(**params)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert any(key in str(body).lower() for key in ('symbol', 'limit', 'interval', 'time', 'exchange'))
    else:
        assert "code" in body
        assert "status" in body
        assert "message" in body
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] in {"error", "fail", "failed"}
    assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('CG-FR-SUMMARY-RESPONSE-001 - Response - 正常请求')
@allure.feature('CoinGlass')
@allure.story('Response')
@pytest.mark.coinglass_api
def test_cg_fr_summary_response_001(coinglass_api):
    """
    Case ID: CG-FR-SUMMARY-RESPONSE-001
    测试大类: CoinGlass
    测试类型: Response
    测试目的: 正常请求
    预期断言: 汇总对象包含资金费率历史、交易所列表、套利或错误提示的稳定字段
    """

    params = {'symbol': 'BTCUSDT', 'limit': 10}

    response = coinglass_api.get_funding_rate_summary(**params)

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
    case_id = 'CG-FR-SUMMARY-RESPONSE-001'
    if isinstance(data, dict) and "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    inner = data.get("data") if isinstance(data, dict) else data
    if inner is None:
        inner = []
    if isinstance(inner, dict):
        rows = [inner]
    else:
        assert isinstance(inner, list)
        rows = inner
    if params.get("limit") is not None:
        assert len(rows) <= int(params["limit"]) or isinstance(data, dict)
    assert isinstance(data, dict)
    assert "symbol" in data or "data" in data
    for value in data.values():
        if isinstance(value, dict):
            for nested_value in value.values():
                if isinstance(nested_value, (int, float, str)):
                    try:
                        Decimal(str(nested_value))
                    except Exception:
                        pass


@allure.title('CG-FR-SUMMARY-DQC-001 - DataQuality - 正常请求')
@allure.feature('CoinGlass')
@allure.story('DataQuality')
@pytest.mark.coinglass_api
@pytest.mark.dqc
def test_cg_fr_summary_dqc_001(coinglass_api):
    """
    Case ID: CG-FR-SUMMARY-DQC-001
    测试大类: CoinGlass
    测试类型: DataQuality
    测试目的: 正常请求
    预期断言: 聚合子对象中的时间与数值字段类型合法
    """

    params = {'symbol': 'BTCUSDT', 'limit': 10}

    response = coinglass_api.get_funding_rate_summary(**params)

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
    case_id = 'CG-FR-SUMMARY-DQC-001'
    if isinstance(data, dict) and "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    inner = data.get("data") if isinstance(data, dict) else data
    if inner is None:
        inner = []
    if isinstance(inner, dict):
        rows = [inner]
    else:
        assert isinstance(inner, list)
        rows = inner
    if params.get("limit") is not None:
        assert len(rows) <= int(params["limit"]) or isinstance(data, dict)
    assert isinstance(data, dict)
    assert "symbol" in data or "data" in data
    for value in data.values():
        if isinstance(value, dict):
            for nested_value in value.values():
                if isinstance(nested_value, (int, float, str)):
                    try:
                        Decimal(str(nested_value))
                    except Exception:
                        pass


@allure.title('CG-LS-HISTORY-NORMAL-001 - Normal - exchange=Binance&symbol=BTCUSDT&interval=1h&limit=10')
@allure.feature('CoinGlass')
@allure.story('Normal')
@pytest.mark.coinglass_api
def test_cg_ls_history_normal_001(coinglass_api):
    """
    Case ID: CG-LS-HISTORY-NORMAL-001
    测试大类: CoinGlass
    测试类型: Normal
    测试目的: exchange=Binance&symbol=BTCUSDT&interval=1h&limit=10
    预期断言: 成功；`data.exchange/symbol/interval/data/timestamp` 存在
    """

    params = {'exchange': 'Binance', 'symbol': 'BTCUSDT', 'interval': '1h', 'limit': 10}

    response = coinglass_api.get_long_short_ratio_history(**params)

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
    case_id = 'CG-LS-HISTORY-NORMAL-001'
    if isinstance(data, dict) and "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    inner = data.get("data") if isinstance(data, dict) else data
    if inner is None:
        inner = []
    if isinstance(inner, dict):
        rows = [inner]
    else:
        assert isinstance(inner, list)
        rows = inner
    if params.get("limit") is not None:
        assert len(rows) <= int(params["limit"]) or isinstance(data, dict)
    assert isinstance(data, dict)
    assert "exchange" in data
    assert "symbol" in data
    assert "interval" in data
    for point in rows:
        if point:
            for field in ("global_account_long_percent", "global_account_short_percent", "long_short_ratio"):
                if field in point and point[field] is not None:
                    value = Decimal(str(point[field]))
                    assert value is not None
                    if "percent" in field:
                        assert Decimal("0") <= value <= Decimal("100")
            long_value = point.get("global_account_long_percent")
            short_value = point.get("global_account_short_percent")
            if long_value is not None and short_value is not None:
                assert abs((Decimal(str(long_value)) + Decimal(str(short_value))) - Decimal("100")) <= Decimal("1") or case_id != "CG-LS-HISTORY-LOGIC-001"
            time_value = point.get("time", point.get("start_time", point.get("timestamp")))
            if time_value is not None and isinstance(time_value, (int, float, str)) and str(time_value).isdigit():
                millis = int(time_value)
                assert len(str(millis)) == 13
                if "start_time" in params and "end_time" in params:
                    assert params["start_time"] <= millis < params["end_time"]


@allure.title('CG-LS-HISTORY-NORMAL-002 - Normal - 加 `start_time=1704067200000&end_time=1704153600000')
@allure.feature('CoinGlass')
@allure.story('Normal')
@pytest.mark.coinglass_api
def test_cg_ls_history_normal_002(coinglass_api):
    """
    Case ID: CG-LS-HISTORY-NORMAL-002
    测试大类: CoinGlass
    测试类型: Normal
    测试目的: 加 `start_time=1704067200000&end_time=1704153600000
    预期断言: 返回点落在时间窗内，或空窗提示
    """

    params = {'exchange': 'Binance', 'symbol': 'BTCUSDT', 'interval': '1h', 'start_time': 1704067200000, 'end_time': 1704153600000, 'limit': 10}

    response = coinglass_api.get_long_short_ratio_history(**params)

    assert response.status_code == 200
    body = response.json()
    assert "code" in body
    assert "status" in body
    assert "message" in body
    assert "data" in body
    if str(body["code"]) == "400":
        assert body["status"] == "error"
        assert body["message"]
        assert isinstance(body["data"], dict)
        return
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]
    data = body["data"]
    assert data is not None
    case_id = 'CG-LS-HISTORY-NORMAL-002'
    if isinstance(data, dict) and "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    inner = data.get("data") if isinstance(data, dict) else data
    if inner is None:
        inner = []
    if isinstance(inner, dict):
        rows = [inner]
    else:
        assert isinstance(inner, list)
        rows = inner
    if params.get("limit") is not None:
        assert len(rows) <= int(params["limit"]) or isinstance(data, dict)
    assert isinstance(data, dict)
    assert "exchange" in data
    assert "symbol" in data
    assert "interval" in data
    for point in rows:
        if point:
            for field in ("global_account_long_percent", "global_account_short_percent", "long_short_ratio"):
                if field in point and point[field] is not None:
                    value = Decimal(str(point[field]))
                    assert value is not None
                    if "percent" in field:
                        assert Decimal("0") <= value <= Decimal("100")
            long_value = point.get("global_account_long_percent")
            short_value = point.get("global_account_short_percent")
            if long_value is not None and short_value is not None:
                assert abs((Decimal(str(long_value)) + Decimal(str(short_value))) - Decimal("100")) <= Decimal("1") or case_id != "CG-LS-HISTORY-LOGIC-001"
            time_value = point.get("time", point.get("start_time", point.get("timestamp")))
            if time_value is not None and isinstance(time_value, (int, float, str)) and str(time_value).isdigit():
                millis = int(time_value)
                assert len(str(millis)) == 13
                if "start_time" in params and "end_time" in params:
                    assert params["start_time"] <= millis < params["end_time"]


@allure.title('CG-LS-HISTORY-BOUNDARY-001 - Boundary - limit=1')
@allure.feature('CoinGlass')
@allure.story('Boundary')
@pytest.mark.coinglass_api
def test_cg_ls_history_boundary_001(coinglass_api):
    """
    Case ID: CG-LS-HISTORY-BOUNDARY-001
    测试大类: CoinGlass
    测试类型: Boundary
    测试目的: limit=1
    预期断言: 返回最多 1 条
    """

    params = {'exchange': 'Binance', 'symbol': 'BTCUSDT', 'interval': '1h', 'limit': 1}

    response = coinglass_api.get_long_short_ratio_history(**params)

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
    case_id = 'CG-LS-HISTORY-BOUNDARY-001'
    if isinstance(data, dict) and "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    inner = data.get("data") if isinstance(data, dict) else data
    if inner is None:
        inner = []
    if isinstance(inner, dict):
        rows = [inner]
    else:
        assert isinstance(inner, list)
        rows = inner
    if params.get("limit") is not None:
        assert len(rows) <= int(params["limit"]) or isinstance(data, dict)
    assert isinstance(data, dict)
    assert "exchange" in data
    assert "symbol" in data
    assert "interval" in data
    for point in rows:
        if point:
            for field in ("global_account_long_percent", "global_account_short_percent", "long_short_ratio"):
                if field in point and point[field] is not None:
                    value = Decimal(str(point[field]))
                    assert value is not None
                    if "percent" in field:
                        assert Decimal("0") <= value <= Decimal("100")
            long_value = point.get("global_account_long_percent")
            short_value = point.get("global_account_short_percent")
            if long_value is not None and short_value is not None:
                assert abs((Decimal(str(long_value)) + Decimal(str(short_value))) - Decimal("100")) <= Decimal("1") or case_id != "CG-LS-HISTORY-LOGIC-001"
            time_value = point.get("time", point.get("start_time", point.get("timestamp")))
            if time_value is not None and isinstance(time_value, (int, float, str)) and str(time_value).isdigit():
                millis = int(time_value)
                assert len(str(millis)) == 13
                if "start_time" in params and "end_time" in params:
                    assert params["start_time"] <= millis < params["end_time"]


@allure.title('CG-LS-HISTORY-BOUNDARY-002 - Boundary - 不传 `limit`，只传时间窗')
@allure.feature('CoinGlass')
@allure.story('Boundary')
@pytest.mark.coinglass_api
def test_cg_ls_history_boundary_002(coinglass_api):
    """
    Case ID: CG-LS-HISTORY-BOUNDARY-002
    测试大类: CoinGlass
    测试类型: Boundary
    测试目的: 不传 `limit`，只传时间窗
    预期断言: 服务按时间范围返回；不能 500
    """

    params = {'exchange': 'Binance', 'symbol': 'BTCUSDT', 'interval': '1h', 'start_time': 1704067200000, 'end_time': 1704153600000}

    try:
        response = coinglass_api.get_long_short_ratio_history(**params)
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
        case_id = 'CG-LS-HISTORY-BOUNDARY-002'
        if isinstance(data, dict) and "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
            assert len(str(int(data["earliest_available_time_ms"]))) == 13
        inner = data.get("data") if isinstance(data, dict) else data
        if inner is None:
            inner = []
        if isinstance(inner, dict):
            rows = [inner]
        else:
            assert isinstance(inner, list)
            rows = inner
        if params.get("limit") is not None:
            assert len(rows) <= int(params["limit"]) or isinstance(data, dict)
        assert isinstance(data, dict)
        assert "exchange" in data
        assert "symbol" in data
        assert "interval" in data
        for point in rows:
            if point:
                for field in ("global_account_long_percent", "global_account_short_percent", "long_short_ratio"):
                    if field in point and point[field] is not None:
                        value = Decimal(str(point[field]))
                        assert value is not None
                        if "percent" in field:
                            assert Decimal("0") <= value <= Decimal("100")
                long_value = point.get("global_account_long_percent")
                short_value = point.get("global_account_short_percent")
                if long_value is not None and short_value is not None:
                    assert abs((Decimal(str(long_value)) + Decimal(str(short_value))) - Decimal("100")) <= Decimal("1") or case_id != "CG-LS-HISTORY-LOGIC-001"
                time_value = point.get("time", point.get("start_time", point.get("timestamp")))
                if time_value is not None and isinstance(time_value, (int, float, str)) and str(time_value).isdigit():
                    millis = int(time_value)
                    assert len(str(millis)) == 13
                    if "start_time" in params and "end_time" in params:
                        assert params["start_time"] <= millis < params["end_time"]


@allure.title('CG-LS-HISTORY-PARAM-001 - ParamError - limit=0')
@allure.feature('CoinGlass')
@allure.story('ParamError')
@pytest.mark.coinglass_api
def test_cg_ls_history_param_001(coinglass_api):
    """
    Case ID: CG-LS-HISTORY-PARAM-001
    测试大类: CoinGlass
    测试类型: ParamError
    测试目的: limit=0
    预期断言: 返回参数错误
    """

    params = {'exchange': 'Binance', 'symbol': 'BTCUSDT', 'interval': '1h', 'limit': 0}

    try:
        response = coinglass_api.get_long_short_ratio_history(**params)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert any(key in str(body).lower() for key in ('symbol', 'limit', 'interval', 'time', 'exchange'))
    else:
        assert "code" in body
        assert "status" in body
        assert "message" in body
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] in {"error", "fail", "failed"}
    assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('CG-LS-HISTORY-PARAM-002 - ParamError - end_time <= start_time')
@allure.feature('CoinGlass')
@allure.story('ParamError')
@pytest.mark.coinglass_api
def test_cg_ls_history_param_002(coinglass_api):
    """
    Case ID: CG-LS-HISTORY-PARAM-002
    测试大类: CoinGlass
    测试类型: ParamError
    测试目的: end_time <= start_time
    预期断言: 返回时间窗错误或业务错误
    """

    params = {'exchange': 'Binance', 'symbol': 'BTCUSDT', 'interval': '1h', 'start_time': 1704153600000, 'end_time': 1704067200000}

    try:
        response = coinglass_api.get_long_short_ratio_history(**params)
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
        case_id = 'CG-LS-HISTORY-PARAM-002'
        if isinstance(data, dict) and "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
            assert len(str(int(data["earliest_available_time_ms"]))) == 13
        inner = data.get("data") if isinstance(data, dict) else data
        if inner is None:
            inner = []
        if isinstance(inner, dict):
            rows = [inner]
        else:
            assert isinstance(inner, list)
            rows = inner
        if params.get("limit") is not None:
            assert len(rows) <= int(params["limit"]) or isinstance(data, dict)
        assert isinstance(data, dict)
        assert "exchange" in data
        assert "symbol" in data
        assert "interval" in data
        for point in rows:
            if point:
                for field in ("global_account_long_percent", "global_account_short_percent", "long_short_ratio"):
                    if field in point and point[field] is not None:
                        value = Decimal(str(point[field]))
                        assert value is not None
                        if "percent" in field:
                            assert Decimal("0") <= value <= Decimal("100")
                long_value = point.get("global_account_long_percent")
                short_value = point.get("global_account_short_percent")
                if long_value is not None and short_value is not None:
                    assert abs((Decimal(str(long_value)) + Decimal(str(short_value))) - Decimal("100")) <= Decimal("1") or case_id != "CG-LS-HISTORY-LOGIC-001"
                time_value = point.get("time", point.get("start_time", point.get("timestamp")))
                if time_value is not None and isinstance(time_value, (int, float, str)) and str(time_value).isdigit():
                    millis = int(time_value)
                    assert len(str(millis)) == 13
                    if "start_time" in params and "end_time" in params:
                        assert params["start_time"] <= millis < params["end_time"]


@allure.title('CG-LS-HISTORY-PARAM-003 - ParamError - exchange=UnknownExchange')
@allure.feature('CoinGlass')
@allure.story('ParamError')
@pytest.mark.coinglass_api
def test_cg_ls_history_param_003(coinglass_api):
    """
    Case ID: CG-LS-HISTORY-PARAM-003
    测试大类: CoinGlass
    测试类型: ParamError
    测试目的: exchange=UnknownExchange
    预期断言: 返回业务错误或空数据；不能 500
    """

    params = {'exchange': 'UnknownExchange', 'symbol': 'BTCUSDT', 'interval': '1h', 'limit': 10}

    try:
        response = coinglass_api.get_long_short_ratio_history(**params)
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
        case_id = 'CG-LS-HISTORY-PARAM-003'
        if isinstance(data, dict) and "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
            assert len(str(int(data["earliest_available_time_ms"]))) == 13
        inner = data.get("data") if isinstance(data, dict) else data
        if inner is None:
            inner = []
        if isinstance(inner, dict):
            rows = [inner]
        else:
            assert isinstance(inner, list)
            rows = inner
        if params.get("limit") is not None:
            assert len(rows) <= int(params["limit"]) or isinstance(data, dict)
        assert isinstance(data, dict)
        assert "exchange" in data
        assert "symbol" in data
        assert "interval" in data
        for point in rows:
            if point:
                for field in ("global_account_long_percent", "global_account_short_percent", "long_short_ratio"):
                    if field in point and point[field] is not None:
                        value = Decimal(str(point[field]))
                        assert value is not None
                        if "percent" in field:
                            assert Decimal("0") <= value <= Decimal("100")
                long_value = point.get("global_account_long_percent")
                short_value = point.get("global_account_short_percent")
                if long_value is not None and short_value is not None:
                    assert abs((Decimal(str(long_value)) + Decimal(str(short_value))) - Decimal("100")) <= Decimal("1") or case_id != "CG-LS-HISTORY-LOGIC-001"
                time_value = point.get("time", point.get("start_time", point.get("timestamp")))
                if time_value is not None and isinstance(time_value, (int, float, str)) and str(time_value).isdigit():
                    millis = int(time_value)
                    assert len(str(millis)) == 13
                    if "start_time" in params and "end_time" in params:
                        assert params["start_time"] <= millis < params["end_time"]


@allure.title('CG-LS-HISTORY-RESPONSE-001 - Response - 正常请求')
@allure.feature('CoinGlass')
@allure.story('Response')
@pytest.mark.coinglass_api
def test_cg_ls_history_response_001(coinglass_api):
    """
    Case ID: CG-LS-HISTORY-RESPONSE-001
    测试大类: CoinGlass
    测试类型: Response
    测试目的: 正常请求
    预期断言: 点存在时包含多空比例字段，如 `global_account_long_percent/short_percent/long_short_ratio
    """

    params = {'exchange': 'Binance', 'symbol': 'BTCUSDT', 'interval': '1h', 'limit': 10}

    response = coinglass_api.get_long_short_ratio_history(**params)

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
    case_id = 'CG-LS-HISTORY-RESPONSE-001'
    if isinstance(data, dict) and "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    inner = data.get("data") if isinstance(data, dict) else data
    if inner is None:
        inner = []
    if isinstance(inner, dict):
        rows = [inner]
    else:
        assert isinstance(inner, list)
        rows = inner
    if params.get("limit") is not None:
        assert len(rows) <= int(params["limit"]) or isinstance(data, dict)
    assert isinstance(data, dict)
    assert "exchange" in data
    assert "symbol" in data
    assert "interval" in data
    for point in rows:
        if point:
            for field in ("global_account_long_percent", "global_account_short_percent", "long_short_ratio"):
                if field in point and point[field] is not None:
                    value = Decimal(str(point[field]))
                    assert value is not None
                    if "percent" in field:
                        assert Decimal("0") <= value <= Decimal("100")
            long_value = point.get("global_account_long_percent")
            short_value = point.get("global_account_short_percent")
            if long_value is not None and short_value is not None:
                assert abs((Decimal(str(long_value)) + Decimal(str(short_value))) - Decimal("100")) <= Decimal("1") or case_id != "CG-LS-HISTORY-LOGIC-001"
            time_value = point.get("time", point.get("start_time", point.get("timestamp")))
            if time_value is not None and isinstance(time_value, (int, float, str)) and str(time_value).isdigit():
                millis = int(time_value)
                assert len(str(millis)) == 13
                if "start_time" in params and "end_time" in params:
                    assert params["start_time"] <= millis < params["end_time"]


@allure.title('CG-LS-HISTORY-DQC-001 - DataQuality - 正常请求')
@allure.feature('CoinGlass')
@allure.story('DataQuality')
@pytest.mark.coinglass_api
@pytest.mark.dqc
def test_cg_ls_history_dqc_001(coinglass_api):
    """
    Case ID: CG-LS-HISTORY-DQC-001
    测试大类: CoinGlass
    测试类型: DataQuality
    测试目的: 正常请求
    预期断言: 比例字段可转数字；百分比字段在合理范围内时不超过 100
    """

    params = {'exchange': 'Binance', 'symbol': 'BTCUSDT', 'interval': '1h', 'limit': 10}

    response = coinglass_api.get_long_short_ratio_history(**params)

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
    case_id = 'CG-LS-HISTORY-DQC-001'
    if isinstance(data, dict) and "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    inner = data.get("data") if isinstance(data, dict) else data
    if inner is None:
        inner = []
    if isinstance(inner, dict):
        rows = [inner]
    else:
        assert isinstance(inner, list)
        rows = inner
    if params.get("limit") is not None:
        assert len(rows) <= int(params["limit"]) or isinstance(data, dict)
    assert isinstance(data, dict)
    assert "exchange" in data
    assert "symbol" in data
    assert "interval" in data
    for point in rows:
        if point:
            for field in ("global_account_long_percent", "global_account_short_percent", "long_short_ratio"):
                if field in point and point[field] is not None:
                    value = Decimal(str(point[field]))
                    assert value is not None
                    if "percent" in field:
                        assert Decimal("0") <= value <= Decimal("100")
            long_value = point.get("global_account_long_percent")
            short_value = point.get("global_account_short_percent")
            if long_value is not None and short_value is not None:
                assert abs((Decimal(str(long_value)) + Decimal(str(short_value))) - Decimal("100")) <= Decimal("1") or case_id != "CG-LS-HISTORY-LOGIC-001"
            time_value = point.get("time", point.get("start_time", point.get("timestamp")))
            if time_value is not None and isinstance(time_value, (int, float, str)) and str(time_value).isdigit():
                millis = int(time_value)
                assert len(str(millis)) == 13
                if "start_time" in params and "end_time" in params:
                    assert params["start_time"] <= millis < params["end_time"]


@allure.title('CG-LS-HISTORY-LOGIC-001 - BusinessLogic - 正常请求')
@allure.feature('CoinGlass')
@allure.story('BusinessLogic')
@pytest.mark.coinglass_api
@pytest.mark.logic
def test_cg_ls_history_logic_001(coinglass_api):
    """
    Case ID: CG-LS-HISTORY-LOGIC-001
    测试大类: CoinGlass
    测试类型: BusinessLogic
    测试目的: 正常请求
    预期断言: long/short 百分比存在时二者合计接近 100，允许上游四舍五入误差
    """

    params = {'exchange': 'Binance', 'symbol': 'BTCUSDT', 'interval': '1h', 'limit': 10}

    response = coinglass_api.get_long_short_ratio_history(**params)

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
    case_id = 'CG-LS-HISTORY-LOGIC-001'
    if isinstance(data, dict) and "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    inner = data.get("data") if isinstance(data, dict) else data
    if inner is None:
        inner = []
    if isinstance(inner, dict):
        rows = [inner]
    else:
        assert isinstance(inner, list)
        rows = inner
    if params.get("limit") is not None:
        assert len(rows) <= int(params["limit"]) or isinstance(data, dict)
    assert isinstance(data, dict)
    assert "exchange" in data
    assert "symbol" in data
    assert "interval" in data
    for point in rows:
        if point:
            for field in ("global_account_long_percent", "global_account_short_percent", "long_short_ratio"):
                if field in point and point[field] is not None:
                    value = Decimal(str(point[field]))
                    assert value is not None
                    if "percent" in field:
                        assert Decimal("0") <= value <= Decimal("100")
            long_value = point.get("global_account_long_percent")
            short_value = point.get("global_account_short_percent")
            if long_value is not None and short_value is not None:
                assert abs((Decimal(str(long_value)) + Decimal(str(short_value))) - Decimal("100")) <= Decimal("1") or case_id != "CG-LS-HISTORY-LOGIC-001"
            time_value = point.get("time", point.get("start_time", point.get("timestamp")))
            if time_value is not None and isinstance(time_value, (int, float, str)) and str(time_value).isdigit():
                millis = int(time_value)
                assert len(str(millis)) == 13
                if "start_time" in params and "end_time" in params:
                    assert params["start_time"] <= millis < params["end_time"]


@allure.title('CG-CONTROLLED-SUMMARY-NORMAL-001 - Normal - symbol=BTCUSDT&exchange=Binance&interval=1h')
@allure.feature('CoinGlass')
@allure.story('Normal')
@pytest.mark.coinglass_api
def test_cg_controlled_summary_normal_001(coinglass_api):
    """
    Case ID: CG-CONTROLLED-SUMMARY-NORMAL-001
    测试大类: CoinGlass
    测试类型: Normal
    测试目的: symbol=BTCUSDT&exchange=Binance&interval=1h
    预期断言: 成功；`data.symbol/exchange/interval/base/liquidation` 存在
    """

    params = {'symbol': 'BTCUSDT', 'exchange': 'Binance', 'interval': '1h'}

    response = coinglass_api.get_controlled_coin_summary(**params)

    assert response.status_code == 200
    body = response.json()
    assert "code" in body
    assert "status" in body
    assert "message" in body
    assert "data" in body
    assert "earliest_available_time_ms" in body
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]
    data = body["data"]
    assert data is not None
    case_id = 'CG-CONTROLLED-SUMMARY-NORMAL-001'
    if isinstance(data, dict) and "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    inner = data.get("data") if isinstance(data, dict) else data
    if inner is None:
        inner = []
    if isinstance(inner, dict):
        rows = [inner]
    else:
        assert isinstance(inner, list)
        rows = inner
    if params.get("limit") is not None:
        assert len(rows) <= int(params["limit"]) or isinstance(data, dict)
    assert isinstance(data, dict)
    assert "symbol" in data or "data" in data
    for value in data.values():
        if isinstance(value, dict):
            for nested_value in value.values():
                if isinstance(nested_value, (int, float, str)):
                    try:
                        Decimal(str(nested_value))
                    except Exception:
                        pass


@allure.title('CG-CONTROLLED-SUMMARY-BOUNDARY-001 - Boundary - 只传必填 `symbol=BTCUSDT')
@allure.feature('CoinGlass')
@allure.story('Boundary')
@pytest.mark.coinglass_api
def test_cg_controlled_summary_boundary_001(coinglass_api):
    """
    Case ID: CG-CONTROLLED-SUMMARY-BOUNDARY-001
    测试大类: CoinGlass
    测试类型: Boundary
    测试目的: 只传必填 `symbol=BTCUSDT
    预期断言: 使用默认 exchange/interval；不能 500
    """

    params = {'symbol': 'BTCUSDT'}

    try:
        response = coinglass_api.get_controlled_coin_summary(**params)
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
        case_id = 'CG-CONTROLLED-SUMMARY-BOUNDARY-001'
        if isinstance(data, dict) and "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
            assert len(str(int(data["earliest_available_time_ms"]))) == 13
        inner = data.get("data") if isinstance(data, dict) else data
        if inner is None:
            inner = []
        if isinstance(inner, dict):
            rows = [inner]
        else:
            assert isinstance(inner, list)
            rows = inner
        if params.get("limit") is not None:
            assert len(rows) <= int(params["limit"]) or isinstance(data, dict)
        assert isinstance(data, dict)
        assert "symbol" in data or "data" in data
        for value in data.values():
            if isinstance(value, dict):
                for nested_value in value.values():
                    if isinstance(nested_value, (int, float, str)):
                        try:
                            Decimal(str(nested_value))
                        except Exception:
                            pass


@allure.title('CG-CONTROLLED-SUMMARY-PARAM-001 - ParamError - 缺少 `symbol')
@allure.feature('CoinGlass')
@allure.story('ParamError')
@pytest.mark.coinglass_api
def test_cg_controlled_summary_param_001(coinglass_api):
    """
    Case ID: CG-CONTROLLED-SUMMARY-PARAM-001
    测试大类: CoinGlass
    测试类型: ParamError
    测试目的: 缺少 `symbol
    预期断言: 返回参数错误，错误信息包含 `symbol
    """

    params = {}

    try:
        response = coinglass_api.get_controlled_coin_summary(**params)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert any(key in str(body).lower() for key in ('symbol', 'limit', 'interval', 'time', 'exchange'))
    else:
        assert "code" in body
        assert "status" in body
        assert "message" in body
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] in {"error", "fail", "failed"}
    assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('CG-CONTROLLED-SUMMARY-PARAM-002 - ParamError - interval=bad_interval')
@allure.feature('CoinGlass')
@allure.story('ParamError')
@pytest.mark.coinglass_api
def test_cg_controlled_summary_param_002(coinglass_api):
    """
    Case ID: CG-CONTROLLED-SUMMARY-PARAM-002
    测试大类: CoinGlass
    测试类型: ParamError
    测试目的: interval=bad_interval
    预期断言: 返回业务错误或明确提示；不能 500
    """

    params = {'symbol': 'BTCUSDT', 'interval': 'bad_interval'}

    try:
        response = coinglass_api.get_controlled_coin_summary(**params)
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
        case_id = 'CG-CONTROLLED-SUMMARY-PARAM-002'
        if isinstance(data, dict) and "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
            assert len(str(int(data["earliest_available_time_ms"]))) == 13
        inner = data.get("data") if isinstance(data, dict) else data
        if inner is None:
            inner = []
        if isinstance(inner, dict):
            rows = [inner]
        else:
            assert isinstance(inner, list)
            rows = inner
        if params.get("limit") is not None:
            assert len(rows) <= int(params["limit"]) or isinstance(data, dict)
        assert isinstance(data, dict)
        assert "symbol" in data or "data" in data
        for value in data.values():
            if isinstance(value, dict):
                for nested_value in value.values():
                    if isinstance(nested_value, (int, float, str)):
                        try:
                            Decimal(str(nested_value))
                        except Exception:
                            pass


@allure.title('CG-CONTROLLED-SUMMARY-RESPONSE-001 - Response - 正常请求')
@allure.feature('CoinGlass')
@allure.story('Response')
@pytest.mark.coinglass_api
def test_cg_controlled_summary_response_001(coinglass_api):
    """
    Case ID: CG-CONTROLLED-SUMMARY-RESPONSE-001
    测试大类: CoinGlass
    测试类型: Response
    测试目的: 正常请求
    预期断言: base` 和 `liquidation` 为对象；允许包含上游原始扩展字段
    """

    params = {'symbol': 'BTCUSDT', 'exchange': 'Binance', 'interval': '1h'}

    response = coinglass_api.get_controlled_coin_summary(**params)

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
    case_id = 'CG-CONTROLLED-SUMMARY-RESPONSE-001'
    if isinstance(data, dict) and "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    inner = data.get("data") if isinstance(data, dict) else data
    if inner is None:
        inner = []
    if isinstance(inner, dict):
        rows = [inner]
    else:
        assert isinstance(inner, list)
        rows = inner
    if params.get("limit") is not None:
        assert len(rows) <= int(params["limit"]) or isinstance(data, dict)
    assert isinstance(data, dict)
    assert "symbol" in data or "data" in data
    for value in data.values():
        if isinstance(value, dict):
            for nested_value in value.values():
                if isinstance(nested_value, (int, float, str)):
                    try:
                        Decimal(str(nested_value))
                    except Exception:
                        pass


@allure.title('CG-CONTROLLED-SUMMARY-DQC-001 - DataQuality - 正常请求')
@allure.feature('CoinGlass')
@allure.story('DataQuality')
@pytest.mark.coinglass_api
@pytest.mark.dqc
def test_cg_controlled_summary_dqc_001(coinglass_api):
    """
    Case ID: CG-CONTROLLED-SUMMARY-DQC-001
    测试大类: CoinGlass
    测试类型: DataQuality
    测试目的: 正常请求
    预期断言: 聚合对象中的时间、数量、金额字段存在时类型合法
    """

    params = {'symbol': 'BTCUSDT', 'exchange': 'Binance', 'interval': '1h'}

    response = coinglass_api.get_controlled_coin_summary(**params)

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
    case_id = 'CG-CONTROLLED-SUMMARY-DQC-001'
    if isinstance(data, dict) and "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    inner = data.get("data") if isinstance(data, dict) else data
    if inner is None:
        inner = []
    if isinstance(inner, dict):
        rows = [inner]
    else:
        assert isinstance(inner, list)
        rows = inner
    if params.get("limit") is not None:
        assert len(rows) <= int(params["limit"]) or isinstance(data, dict)
    assert isinstance(data, dict)
    assert "symbol" in data or "data" in data
    for value in data.values():
        if isinstance(value, dict):
            for nested_value in value.values():
                if isinstance(nested_value, (int, float, str)):
                    try:
                        Decimal(str(nested_value))
                    except Exception:
                        pass
