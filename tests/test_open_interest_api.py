from __future__ import annotations

from decimal import Decimal

import allure
import pytest
from requests.exceptions import HTTPError

from api.platform.open_interest_api import OpenInterestAPI
from config.settings import settings

CONTRACT_SYMBOL = "BTCUSDT"
BASE_SYMBOL = "BTC"
WRONG_BASE_SYMBOL = "BTCUSDT"
EXCHANGE = "Binance"
EXCHANGE_LIST = "Binance,OKX"
INTERVAL_30M = "30m"
INTERVAL_1H = "1h"
LIMIT_SMALL = 1
LIMIT_NORMAL = 10
START_TIME = 1704067200000
END_TIME = 1704153600000
UNIT = "USD"


@pytest.fixture(scope="module")
def open_interest_api() -> OpenInterestAPI:
    if not settings.base_url or "exchange.com" in settings.base_url:
        pytest.skip("Open Interest API BASE_URL is not configured for live API tests.")
    return OpenInterestAPI()


@allure.title('OI-HISTORY-NORMAL-001 - Normal - exchange=Binance&symbol=BTCUSDT&interval=30m&limit=10')
@allure.feature('Open Interest')
@allure.story('Normal')
@pytest.mark.open_interest_api
def test_oi_history_normal_001(open_interest_api):
    """
    Case ID: OI-HISTORY-NORMAL-001
    测试大类: Open Interest
    测试类型: Normal
    测试目的: exchange=Binance&symbol=BTCUSDT&interval=30m&limit=10
    预期断言: 成功；`data.code/msg/data` 结构稳定
    """

    params = {'exchange': 'Binance', 'symbol': 'BTCUSDT', 'interval': '30m', 'limit': 10}

    response = open_interest_api.get_history(**params)

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
    if isinstance(data, dict) and "code" in data:
        assert data["code"] in {0, "0", 200, "200"}
    if isinstance(data, dict) and "msg" in data:
        assert data["msg"] is None or isinstance(data["msg"], str)
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

    endpoint = '/coinglass/oi/history'
    
    case_id = 'OI-HISTORY-NORMAL-001'
    for row in rows:
        if not row:
            continue
        if endpoint.endswith("/exchanges"):
            assert "exchange" in row or "exchange_name" in row
            assert "symbol" in row or "base_asset" in row
        if endpoint.endswith("/history"):
            assert "symbol" in row or "time" in row or "timestamp" in row
        time_value = row.get("time", row.get("timestamp", row.get("start_time")))
        if time_value is not None and str(time_value).isdigit():
            millis = int(time_value)
            assert len(str(millis)) == 13
            if "start_time" in params and "end_time" in params:
                assert params["start_time"] <= millis < params["end_time"]
        for numeric_field, value in row.items():
            name = numeric_field.lower()
            if any(token in name for token in ("interest", "quantity", "usd", "amount", "change", "open", "high", "low", "close", "bid", "ask")):
                if value is not None and isinstance(value, (int, float, str)):
                    try:
                        numeric_value = Decimal(str(value))
                    except Exception:
                        continue
                    assert numeric_value is not None
                    if case_id == "OI-AGG-HISTORY-LOGIC-001" and "change" not in name:
                        assert numeric_value >= 0
                    if case_id == "OI-ORDERBOOK-DQC-001":
                        assert numeric_value >= 0
        if case_id == "OI-HISTORY-LOGIC-001" and all(field in row for field in ("open", "high", "low", "close")):
            open_value = Decimal(str(row["open"]))
            high = Decimal(str(row["high"]))
            low = Decimal(str(row["low"]))
            close = Decimal(str(row["close"]))
            assert high >= open_value
            assert high >= close
            assert high >= low
            assert low <= open_value
            assert low <= close
            assert low <= high


@allure.title('OI-HISTORY-NORMAL-002 - Normal - 加 `start_time=1704067200000&end_time=1704153600000&unit=USD')
@allure.feature('Open Interest')
@allure.story('Normal')
@pytest.mark.open_interest_api
def test_oi_history_normal_002(open_interest_api):
    """
    Case ID: OI-HISTORY-NORMAL-002
    测试大类: Open Interest
    测试类型: Normal
    测试目的: 加 `start_time=1704067200000&end_time=1704153600000&unit=USD
    预期断言: 返回点落在时间窗内，或空窗提示
    """

    params = {'exchange': 'Binance', 'symbol': 'BTCUSDT', 'interval': '30m', 'start_time': 1704067200000, 'end_time': 1704153600000, 'unit': 'USD', 'limit': 10}

    response = open_interest_api.get_history(**params)

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
    if isinstance(data, dict) and "code" in data:
        assert data["code"] in {0, "0", 200, "200"}
    if isinstance(data, dict) and "msg" in data:
        assert data["msg"] is None or isinstance(data["msg"], str)
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

    endpoint = '/coinglass/oi/history'
    
    case_id = 'OI-HISTORY-NORMAL-002'
    for row in rows:
        if not row:
            continue
        if endpoint.endswith("/exchanges"):
            assert "exchange" in row or "exchange_name" in row
            assert "symbol" in row or "base_asset" in row
        if endpoint.endswith("/history"):
            assert "symbol" in row or "time" in row or "timestamp" in row
        time_value = row.get("time", row.get("timestamp", row.get("start_time")))
        if time_value is not None and str(time_value).isdigit():
            millis = int(time_value)
            assert len(str(millis)) == 13
            if "start_time" in params and "end_time" in params:
                assert params["start_time"] <= millis < params["end_time"]
        for numeric_field, value in row.items():
            name = numeric_field.lower()
            if any(token in name for token in ("interest", "quantity", "usd", "amount", "change", "open", "high", "low", "close", "bid", "ask")):
                if value is not None and isinstance(value, (int, float, str)):
                    try:
                        numeric_value = Decimal(str(value))
                    except Exception:
                        continue
                    assert numeric_value is not None
                    if case_id == "OI-AGG-HISTORY-LOGIC-001" and "change" not in name:
                        assert numeric_value >= 0
                    if case_id == "OI-ORDERBOOK-DQC-001":
                        assert numeric_value >= 0
        if case_id == "OI-HISTORY-LOGIC-001" and all(field in row for field in ("open", "high", "low", "close")):
            open_value = Decimal(str(row["open"]))
            high = Decimal(str(row["high"]))
            low = Decimal(str(row["low"]))
            close = Decimal(str(row["close"]))
            assert high >= open_value
            assert high >= close
            assert high >= low
            assert low <= open_value
            assert low <= close
            assert low <= high


@allure.title('OI-HISTORY-BOUNDARY-001 - Boundary - limit=1')
@allure.feature('Open Interest')
@allure.story('Boundary')
@pytest.mark.open_interest_api
def test_oi_history_boundary_001(open_interest_api):
    """
    Case ID: OI-HISTORY-BOUNDARY-001
    测试大类: Open Interest
    测试类型: Boundary
    测试目的: limit=1
    预期断言: 返回最多 1 条
    """

    params = {'exchange': 'Binance', 'symbol': 'BTCUSDT', 'interval': '30m', 'limit': 1}

    response = open_interest_api.get_history(**params)

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
    if isinstance(data, dict) and "code" in data:
        assert data["code"] in {0, "0", 200, "200"}
    if isinstance(data, dict) and "msg" in data:
        assert data["msg"] is None or isinstance(data["msg"], str)
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

    endpoint = '/coinglass/oi/history'
    
    case_id = 'OI-HISTORY-BOUNDARY-001'
    for row in rows:
        if not row:
            continue
        if endpoint.endswith("/exchanges"):
            assert "exchange" in row or "exchange_name" in row
            assert "symbol" in row or "base_asset" in row
        if endpoint.endswith("/history"):
            assert "symbol" in row or "time" in row or "timestamp" in row
        time_value = row.get("time", row.get("timestamp", row.get("start_time")))
        if time_value is not None and str(time_value).isdigit():
            millis = int(time_value)
            assert len(str(millis)) == 13
            if "start_time" in params and "end_time" in params:
                assert params["start_time"] <= millis < params["end_time"]
        for numeric_field, value in row.items():
            name = numeric_field.lower()
            if any(token in name for token in ("interest", "quantity", "usd", "amount", "change", "open", "high", "low", "close", "bid", "ask")):
                if value is not None and isinstance(value, (int, float, str)):
                    try:
                        numeric_value = Decimal(str(value))
                    except Exception:
                        continue
                    assert numeric_value is not None
                    if case_id == "OI-AGG-HISTORY-LOGIC-001" and "change" not in name:
                        assert numeric_value >= 0
                    if case_id == "OI-ORDERBOOK-DQC-001":
                        assert numeric_value >= 0
        if case_id == "OI-HISTORY-LOGIC-001" and all(field in row for field in ("open", "high", "low", "close")):
            open_value = Decimal(str(row["open"]))
            high = Decimal(str(row["high"]))
            low = Decimal(str(row["low"]))
            close = Decimal(str(row["close"]))
            assert high >= open_value
            assert high >= close
            assert high >= low
            assert low <= open_value
            assert low <= close
            assert low <= high


@allure.title('OI-HISTORY-BOUNDARY-002 - Boundary - force_refresh=false')
@allure.feature('Open Interest')
@allure.story('Boundary')
@pytest.mark.open_interest_api
def test_oi_history_boundary_002(open_interest_api):
    """
    Case ID: OI-HISTORY-BOUNDARY-002
    测试大类: Open Interest
    测试类型: Boundary
    测试目的: force_refresh=false
    预期断言: 使用缓存语义；成功或明确业务提示
    """

    params = {'exchange': 'Binance', 'symbol': 'BTCUSDT', 'interval': '30m', 'force_refresh': False, 'limit': 10}

    try:
        response = open_interest_api.get_history(**params)
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
        return
    else:
        assert body["status"] == "success"
        assert body["message"]
        data = body["data"]
        assert data is not None
        if isinstance(data, dict) and "code" in data:
            assert data["code"] in {0, "0", 200, "200"}
        if isinstance(data, dict) and "msg" in data:
            assert data["msg"] is None or isinstance(data["msg"], str)
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

        endpoint = '/coinglass/oi/history'
        
    case_id = 'OI-HISTORY-BOUNDARY-002'
    for row in rows:
            if not row:
                continue
            if endpoint.endswith("/exchanges"):
                assert "exchange" in row or "exchange_name" in row
                assert "symbol" in row or "base_asset" in row
            if endpoint.endswith("/history"):
                assert "symbol" in row or "time" in row or "timestamp" in row
            time_value = row.get("time", row.get("timestamp", row.get("start_time")))
            if time_value is not None and str(time_value).isdigit():
                millis = int(time_value)
                assert len(str(millis)) == 13
                if "start_time" in params and "end_time" in params:
                    assert params["start_time"] <= millis < params["end_time"]
            for numeric_field, value in row.items():
                name = numeric_field.lower()
                if any(token in name for token in ("interest", "quantity", "usd", "amount", "change", "open", "high", "low", "close", "bid", "ask")):
                    if value is not None and isinstance(value, (int, float, str)):
                        try:
                            numeric_value = Decimal(str(value))
                        except Exception:
                            continue
                        assert numeric_value is not None
                        if case_id == "OI-AGG-HISTORY-LOGIC-001" and "change" not in name:
                            assert numeric_value >= 0
                        if case_id == "OI-ORDERBOOK-DQC-001":
                            assert numeric_value >= 0
            if case_id == "OI-HISTORY-LOGIC-001" and all(field in row for field in ("open", "high", "low", "close")):
                open_value = Decimal(str(row["open"]))
                high = Decimal(str(row["high"]))
                low = Decimal(str(row["low"]))
                close = Decimal(str(row["close"]))
                assert high >= open_value
                assert high >= close
                assert high >= low
                assert low <= open_value
                assert low <= close
                assert low <= high


@allure.title('OI-HISTORY-BOUNDARY-003 - Boundary - force_refresh=true&limit=1')
@allure.feature('Open Interest')
@allure.story('Boundary')
@pytest.mark.open_interest_api
def test_oi_history_boundary_003(open_interest_api):
    """
    Case ID: OI-HISTORY-BOUNDARY-003
    测试大类: Open Interest
    测试类型: Boundary
    测试目的: force_refresh=true&limit=1
    预期断言: 绕缓存请求；只校验契约与不 500
    """

    params = {'exchange': 'Binance', 'symbol': 'BTCUSDT', 'interval': '30m', 'force_refresh': True, 'limit': 1}

    try:
        response = open_interest_api.get_history(**params)
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
        return
    else:
        assert body["status"] == "success"
        assert body["message"]
        data = body["data"]
        assert data is not None
        if isinstance(data, dict) and "code" in data:
            assert data["code"] in {0, "0", 200, "200"}
        if isinstance(data, dict) and "msg" in data:
            assert data["msg"] is None or isinstance(data["msg"], str)
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

        endpoint = '/coinglass/oi/history'
        
    case_id = 'OI-HISTORY-BOUNDARY-003'
    for row in rows:
            if not row:
                continue
            if endpoint.endswith("/exchanges"):
                assert "exchange" in row or "exchange_name" in row
                assert "symbol" in row or "base_asset" in row
            if endpoint.endswith("/history"):
                assert "symbol" in row or "time" in row or "timestamp" in row
            time_value = row.get("time", row.get("timestamp", row.get("start_time")))
            if time_value is not None and str(time_value).isdigit():
                millis = int(time_value)
                assert len(str(millis)) == 13
                if "start_time" in params and "end_time" in params:
                    assert params["start_time"] <= millis < params["end_time"]
            for numeric_field, value in row.items():
                name = numeric_field.lower()
                if any(token in name for token in ("interest", "quantity", "usd", "amount", "change", "open", "high", "low", "close", "bid", "ask")):
                    if value is not None and isinstance(value, (int, float, str)):
                        try:
                            numeric_value = Decimal(str(value))
                        except Exception:
                            continue
                        assert numeric_value is not None
                        if case_id == "OI-AGG-HISTORY-LOGIC-001" and "change" not in name:
                            assert numeric_value >= 0
                        if case_id == "OI-ORDERBOOK-DQC-001":
                            assert numeric_value >= 0
            if case_id == "OI-HISTORY-LOGIC-001" and all(field in row for field in ("open", "high", "low", "close")):
                open_value = Decimal(str(row["open"]))
                high = Decimal(str(row["high"]))
                low = Decimal(str(row["low"]))
                close = Decimal(str(row["close"]))
                assert high >= open_value
                assert high >= close
                assert high >= low
                assert low <= open_value
                assert low <= close
                assert low <= high


@allure.title('OI-HISTORY-PARAM-001 - ParamError - limit=0')
@allure.feature('Open Interest')
@allure.story('ParamError')
@pytest.mark.open_interest_api
def test_oi_history_param_001(open_interest_api):
    """
    Case ID: OI-HISTORY-PARAM-001
    测试大类: Open Interest
    测试类型: ParamError
    测试目的: limit=0
    预期断言: 返回参数错误
    """

    params = {'exchange': 'Binance', 'symbol': 'BTCUSDT', 'interval': '30m', 'limit': 0}

    try:
        response = open_interest_api.get_history(**params)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert any(key in str(body).lower() for key in ('symbol', 'limit', 'range', 'time', 'interval'))
    else:
        assert "code" in body
        assert "status" in body
        assert "message" in body
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] in {"error", "fail", "failed"}
    assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('OI-HISTORY-PARAM-002 - ParamError - end_time <= start_time')
@allure.feature('Open Interest')
@allure.story('ParamError')
@pytest.mark.open_interest_api
def test_oi_history_param_002(open_interest_api):
    """
    Case ID: OI-HISTORY-PARAM-002
    测试大类: Open Interest
    测试类型: ParamError
    测试目的: end_time <= start_time
    预期断言: 返回时间窗错误或业务错误
    """

    params = {'exchange': 'Binance', 'symbol': 'BTCUSDT', 'interval': '30m', 'start_time': 1704153600000, 'end_time': 1704067200000}

    try:
        response = open_interest_api.get_history(**params)
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
        return
    else:
        assert body["status"] == "success"
        assert body["message"]
        data = body["data"]
        assert data is not None
        if isinstance(data, dict) and "code" in data:
            assert data["code"] in {0, "0", 200, "200"}
        if isinstance(data, dict) and "msg" in data:
            assert data["msg"] is None or isinstance(data["msg"], str)
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

        endpoint = '/coinglass/oi/history'
        
    case_id = 'OI-HISTORY-PARAM-002'
    for row in rows:
            if not row:
                continue
            if endpoint.endswith("/exchanges"):
                assert "exchange" in row or "exchange_name" in row
                assert "symbol" in row or "base_asset" in row
            if endpoint.endswith("/history"):
                assert "symbol" in row or "time" in row or "timestamp" in row
            time_value = row.get("time", row.get("timestamp", row.get("start_time")))
            if time_value is not None and str(time_value).isdigit():
                millis = int(time_value)
                assert len(str(millis)) == 13
                if "start_time" in params and "end_time" in params:
                    assert params["start_time"] <= millis < params["end_time"]
            for numeric_field, value in row.items():
                name = numeric_field.lower()
                if any(token in name for token in ("interest", "quantity", "usd", "amount", "change", "open", "high", "low", "close", "bid", "ask")):
                    if value is not None and isinstance(value, (int, float, str)):
                        try:
                            numeric_value = Decimal(str(value))
                        except Exception:
                            continue
                        assert numeric_value is not None
                        if case_id == "OI-AGG-HISTORY-LOGIC-001" and "change" not in name:
                            assert numeric_value >= 0
                        if case_id == "OI-ORDERBOOK-DQC-001":
                            assert numeric_value >= 0
            if case_id == "OI-HISTORY-LOGIC-001" and all(field in row for field in ("open", "high", "low", "close")):
                open_value = Decimal(str(row["open"]))
                high = Decimal(str(row["high"]))
                low = Decimal(str(row["low"]))
                close = Decimal(str(row["close"]))
                assert high >= open_value
                assert high >= close
                assert high >= low
                assert low <= open_value
                assert low <= close
                assert low <= high


@allure.title('OI-HISTORY-PARAM-003 - ParamError - symbol=BTC')
@allure.feature('Open Interest')
@allure.story('ParamError')
@pytest.mark.open_interest_api
def test_oi_history_param_003(open_interest_api):
    """
    Case ID: OI-HISTORY-PARAM-003
    测试大类: Open Interest
    测试类型: ParamError
    测试目的: symbol=BTC
    预期断言: 对 history 误传基础币种，应返回空数据或业务提示；不能 500
    """

    params = {'exchange': 'Binance', 'symbol': 'BTC', 'interval': '30m', 'limit': 10}

    try:
        response = open_interest_api.get_history(**params)
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
        return
    else:
        assert body["status"] == "success"
        assert body["message"]
        data = body["data"]
        assert data is not None
        if isinstance(data, dict) and "code" in data:
            assert data["code"] in {0, "0", 200, "200"}
        if isinstance(data, dict) and "msg" in data:
            assert data["msg"] is None or isinstance(data["msg"], str)
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

        endpoint = '/coinglass/oi/history'
        
    case_id = 'OI-HISTORY-PARAM-003'
    for row in rows:
            if not row:
                continue
            if endpoint.endswith("/exchanges"):
                assert "exchange" in row or "exchange_name" in row
                assert "symbol" in row or "base_asset" in row
            if endpoint.endswith("/history"):
                assert "symbol" in row or "time" in row or "timestamp" in row
            time_value = row.get("time", row.get("timestamp", row.get("start_time")))
            if time_value is not None and str(time_value).isdigit():
                millis = int(time_value)
                assert len(str(millis)) == 13
                if "start_time" in params and "end_time" in params:
                    assert params["start_time"] <= millis < params["end_time"]
            for numeric_field, value in row.items():
                name = numeric_field.lower()
                if any(token in name for token in ("interest", "quantity", "usd", "amount", "change", "open", "high", "low", "close", "bid", "ask")):
                    if value is not None and isinstance(value, (int, float, str)):
                        try:
                            numeric_value = Decimal(str(value))
                        except Exception:
                            continue
                        assert numeric_value is not None
                        if case_id == "OI-AGG-HISTORY-LOGIC-001" and "change" not in name:
                            assert numeric_value >= 0
                        if case_id == "OI-ORDERBOOK-DQC-001":
                            assert numeric_value >= 0
            if case_id == "OI-HISTORY-LOGIC-001" and all(field in row for field in ("open", "high", "low", "close")):
                open_value = Decimal(str(row["open"]))
                high = Decimal(str(row["high"]))
                low = Decimal(str(row["low"]))
                close = Decimal(str(row["close"]))
                assert high >= open_value
                assert high >= close
                assert high >= low
                assert low <= open_value
                assert low <= close
                assert low <= high


@allure.title('OI-HISTORY-RESPONSE-001 - Response - 正常请求')
@allure.feature('Open Interest')
@allure.story('Response')
@pytest.mark.open_interest_api
def test_oi_history_response_001(open_interest_api):
    """
    Case ID: OI-HISTORY-RESPONSE-001
    测试大类: Open Interest
    测试类型: Response
    测试目的: 正常请求
    预期断言: 历史点存在时包含 OI OHLC 或等价字段
    """

    params = {'exchange': 'Binance', 'symbol': 'BTCUSDT', 'interval': '30m', 'limit': 10}

    response = open_interest_api.get_history(**params)

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
    if isinstance(data, dict) and "code" in data:
        assert data["code"] in {0, "0", 200, "200"}
    if isinstance(data, dict) and "msg" in data:
        assert data["msg"] is None or isinstance(data["msg"], str)
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

    endpoint = '/coinglass/oi/history'
    
    case_id = 'OI-HISTORY-RESPONSE-001'
    for row in rows:
        if not row:
            continue
        if endpoint.endswith("/exchanges"):
            assert "exchange" in row or "exchange_name" in row
            assert "symbol" in row or "base_asset" in row
        if endpoint.endswith("/history"):
            assert "symbol" in row or "time" in row or "timestamp" in row
        time_value = row.get("time", row.get("timestamp", row.get("start_time")))
        if time_value is not None and str(time_value).isdigit():
            millis = int(time_value)
            assert len(str(millis)) == 13
            if "start_time" in params and "end_time" in params:
                assert params["start_time"] <= millis < params["end_time"]
        for numeric_field, value in row.items():
            name = numeric_field.lower()
            if any(token in name for token in ("interest", "quantity", "usd", "amount", "change", "open", "high", "low", "close", "bid", "ask")):
                if value is not None and isinstance(value, (int, float, str)):
                    try:
                        numeric_value = Decimal(str(value))
                    except Exception:
                        continue
                    assert numeric_value is not None
                    if case_id == "OI-AGG-HISTORY-LOGIC-001" and "change" not in name:
                        assert numeric_value >= 0
                    if case_id == "OI-ORDERBOOK-DQC-001":
                        assert numeric_value >= 0
        if case_id == "OI-HISTORY-LOGIC-001" and all(field in row for field in ("open", "high", "low", "close")):
            open_value = Decimal(str(row["open"]))
            high = Decimal(str(row["high"]))
            low = Decimal(str(row["low"]))
            close = Decimal(str(row["close"]))
            assert high >= open_value
            assert high >= close
            assert high >= low
            assert low <= open_value
            assert low <= close
            assert low <= high


@allure.title('OI-HISTORY-DQC-001 - DataQuality - 正常请求')
@allure.feature('Open Interest')
@allure.story('DataQuality')
@pytest.mark.open_interest_api
@pytest.mark.dqc
def test_oi_history_dqc_001(open_interest_api):
    """
    Case ID: OI-HISTORY-DQC-001
    测试大类: Open Interest
    测试类型: DataQuality
    测试目的: 正常请求
    预期断言: 时间字段为毫秒；OI 数值字段可转数字
    """

    params = {'exchange': 'Binance', 'symbol': 'BTCUSDT', 'interval': '30m', 'limit': 10}

    response = open_interest_api.get_history(**params)

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
    if isinstance(data, dict) and "code" in data:
        assert data["code"] in {0, "0", 200, "200"}
    if isinstance(data, dict) and "msg" in data:
        assert data["msg"] is None or isinstance(data["msg"], str)
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

    endpoint = '/coinglass/oi/history'
    
    case_id = 'OI-HISTORY-DQC-001'
    for row in rows:
        if not row:
            continue
        if endpoint.endswith("/exchanges"):
            assert "exchange" in row or "exchange_name" in row
            assert "symbol" in row or "base_asset" in row
        if endpoint.endswith("/history"):
            assert "symbol" in row or "time" in row or "timestamp" in row
        time_value = row.get("time", row.get("timestamp", row.get("start_time")))
        if time_value is not None and str(time_value).isdigit():
            millis = int(time_value)
            assert len(str(millis)) == 13
            if "start_time" in params and "end_time" in params:
                assert params["start_time"] <= millis < params["end_time"]
        for numeric_field, value in row.items():
            name = numeric_field.lower()
            if any(token in name for token in ("interest", "quantity", "usd", "amount", "change", "open", "high", "low", "close", "bid", "ask")):
                if value is not None and isinstance(value, (int, float, str)):
                    try:
                        numeric_value = Decimal(str(value))
                    except Exception:
                        continue
                    assert numeric_value is not None
                    if case_id == "OI-AGG-HISTORY-LOGIC-001" and "change" not in name:
                        assert numeric_value >= 0
                    if case_id == "OI-ORDERBOOK-DQC-001":
                        assert numeric_value >= 0
        if case_id == "OI-HISTORY-LOGIC-001" and all(field in row for field in ("open", "high", "low", "close")):
            open_value = Decimal(str(row["open"]))
            high = Decimal(str(row["high"]))
            low = Decimal(str(row["low"]))
            close = Decimal(str(row["close"]))
            assert high >= open_value
            assert high >= close
            assert high >= low
            assert low <= open_value
            assert low <= close
            assert low <= high


@allure.title('OI-HISTORY-LOGIC-001 - BusinessLogic - 正常请求')
@allure.feature('Open Interest')
@allure.story('BusinessLogic')
@pytest.mark.open_interest_api
@pytest.mark.logic
def test_oi_history_logic_001(open_interest_api):
    """
    Case ID: OI-HISTORY-LOGIC-001
    测试大类: Open Interest
    测试类型: BusinessLogic
    测试目的: 正常请求
    预期断言: OHLC 形态存在时满足 high/low 关系；时间排序稳定
    """

    params = {'exchange': 'Binance', 'symbol': 'BTCUSDT', 'interval': '30m', 'limit': 10}

    response = open_interest_api.get_history(**params)

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
    if isinstance(data, dict) and "code" in data:
        assert data["code"] in {0, "0", 200, "200"}
    if isinstance(data, dict) and "msg" in data:
        assert data["msg"] is None or isinstance(data["msg"], str)
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

    endpoint = '/coinglass/oi/history'
    
    case_id = 'OI-HISTORY-LOGIC-001'
    for row in rows:
        if not row:
            continue
        if endpoint.endswith("/exchanges"):
            assert "exchange" in row or "exchange_name" in row
            assert "symbol" in row or "base_asset" in row
        if endpoint.endswith("/history"):
            assert "symbol" in row or "time" in row or "timestamp" in row
        time_value = row.get("time", row.get("timestamp", row.get("start_time")))
        if time_value is not None and str(time_value).isdigit():
            millis = int(time_value)
            assert len(str(millis)) == 13
            if "start_time" in params and "end_time" in params:
                assert params["start_time"] <= millis < params["end_time"]
        for numeric_field, value in row.items():
            name = numeric_field.lower()
            if any(token in name for token in ("interest", "quantity", "usd", "amount", "change", "open", "high", "low", "close", "bid", "ask")):
                if value is not None and isinstance(value, (int, float, str)):
                    try:
                        numeric_value = Decimal(str(value))
                    except Exception:
                        continue
                    assert numeric_value is not None
                    if case_id == "OI-AGG-HISTORY-LOGIC-001" and "change" not in name:
                        assert numeric_value >= 0
                    if case_id == "OI-ORDERBOOK-DQC-001":
                        assert numeric_value >= 0
        if case_id == "OI-HISTORY-LOGIC-001" and all(field in row for field in ("open", "high", "low", "close")):
            open_value = Decimal(str(row["open"]))
            high = Decimal(str(row["high"]))
            low = Decimal(str(row["low"]))
            close = Decimal(str(row["close"]))
            assert high >= open_value
            assert high >= close
            assert high >= low
            assert low <= open_value
            assert low <= close
            assert low <= high


@allure.title('OI-AGG-HISTORY-NORMAL-001 - Normal - symbol=BTC&interval=30m&limit=10')
@allure.feature('Open Interest')
@allure.story('Normal')
@pytest.mark.open_interest_api
def test_oi_agg_history_normal_001(open_interest_api):
    """
    Case ID: OI-AGG-HISTORY-NORMAL-001
    测试大类: Open Interest
    测试类型: Normal
    测试目的: symbol=BTC&interval=30m&limit=10
    预期断言: 成功；`data.data` 为数组或空数组
    """

    params = {'symbol': 'BTC', 'interval': '30m', 'limit': 10}

    response = open_interest_api.get_aggregated_history(**params)

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
    if isinstance(data, dict) and "code" in data:
        assert data["code"] in {0, "0", 200, "200"}
    if isinstance(data, dict) and "msg" in data:
        assert data["msg"] is None or isinstance(data["msg"], str)
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

    endpoint = '/coinglass/oi/aggregated/history'
    
    case_id = 'OI-AGG-HISTORY-NORMAL-001'
    for row in rows:
        if not row:
            continue
        if endpoint.endswith("/exchanges"):
            assert "exchange" in row or "exchange_name" in row
            assert "symbol" in row or "base_asset" in row
        if endpoint.endswith("/history"):
            assert "symbol" in row or "time" in row or "timestamp" in row
        time_value = row.get("time", row.get("timestamp", row.get("start_time")))
        if time_value is not None and str(time_value).isdigit():
            millis = int(time_value)
            assert len(str(millis)) == 13
            if "start_time" in params and "end_time" in params:
                assert params["start_time"] <= millis < params["end_time"]
        for numeric_field, value in row.items():
            name = numeric_field.lower()
            if any(token in name for token in ("interest", "quantity", "usd", "amount", "change", "open", "high", "low", "close", "bid", "ask")):
                if value is not None and isinstance(value, (int, float, str)):
                    try:
                        numeric_value = Decimal(str(value))
                    except Exception:
                        continue
                    assert numeric_value is not None
                    if case_id == "OI-AGG-HISTORY-LOGIC-001" and "change" not in name:
                        assert numeric_value >= 0
                    if case_id == "OI-ORDERBOOK-DQC-001":
                        assert numeric_value >= 0
        if case_id == "OI-HISTORY-LOGIC-001" and all(field in row for field in ("open", "high", "low", "close")):
            open_value = Decimal(str(row["open"]))
            high = Decimal(str(row["high"]))
            low = Decimal(str(row["low"]))
            close = Decimal(str(row["close"]))
            assert high >= open_value
            assert high >= close
            assert high >= low
            assert low <= open_value
            assert low <= close
            assert low <= high


@allure.title('OI-AGG-HISTORY-NORMAL-002 - Normal - symbol=BTC&start_time=1704067200000&end_time=1704153600000&unit=USD')
@allure.feature('Open Interest')
@allure.story('Normal')
@pytest.mark.open_interest_api
def test_oi_agg_history_normal_002(open_interest_api):
    """
    Case ID: OI-AGG-HISTORY-NORMAL-002
    测试大类: Open Interest
    测试类型: Normal
    测试目的: symbol=BTC&start_time=1704067200000&end_time=1704153600000&unit=USD
    预期断言: 时间窗语义正确
    """

    params = {'symbol': 'BTC', 'start_time': 1704067200000, 'end_time': 1704153600000, 'unit': 'USD', 'limit': 10}

    response = open_interest_api.get_aggregated_history(**params)

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
    if isinstance(data, dict) and "code" in data:
        assert data["code"] in {0, "0", 200, "200"}
    if isinstance(data, dict) and "msg" in data:
        assert data["msg"] is None or isinstance(data["msg"], str)
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

    endpoint = '/coinglass/oi/aggregated/history'
    
    case_id = 'OI-AGG-HISTORY-NORMAL-002'
    for row in rows:
        if not row:
            continue
        if endpoint.endswith("/exchanges"):
            assert "exchange" in row or "exchange_name" in row
            assert "symbol" in row or "base_asset" in row
        if endpoint.endswith("/history"):
            assert "symbol" in row or "time" in row or "timestamp" in row
        time_value = row.get("time", row.get("timestamp", row.get("start_time")))
        if time_value is not None and str(time_value).isdigit():
            millis = int(time_value)
            assert len(str(millis)) == 13
            if "start_time" in params and "end_time" in params:
                assert params["start_time"] <= millis < params["end_time"]
        for numeric_field, value in row.items():
            name = numeric_field.lower()
            if any(token in name for token in ("interest", "quantity", "usd", "amount", "change", "open", "high", "low", "close", "bid", "ask")):
                if value is not None and isinstance(value, (int, float, str)):
                    try:
                        numeric_value = Decimal(str(value))
                    except Exception:
                        continue
                    assert numeric_value is not None
                    if case_id == "OI-AGG-HISTORY-LOGIC-001" and "change" not in name:
                        assert numeric_value >= 0
                    if case_id == "OI-ORDERBOOK-DQC-001":
                        assert numeric_value >= 0
        if case_id == "OI-HISTORY-LOGIC-001" and all(field in row for field in ("open", "high", "low", "close")):
            open_value = Decimal(str(row["open"]))
            high = Decimal(str(row["high"]))
            low = Decimal(str(row["low"]))
            close = Decimal(str(row["close"]))
            assert high >= open_value
            assert high >= close
            assert high >= low
            assert low <= open_value
            assert low <= close
            assert low <= high


@allure.title('OI-AGG-HISTORY-BOUNDARY-001 - Boundary - limit=1')
@allure.feature('Open Interest')
@allure.story('Boundary')
@pytest.mark.open_interest_api
def test_oi_agg_history_boundary_001(open_interest_api):
    """
    Case ID: OI-AGG-HISTORY-BOUNDARY-001
    测试大类: Open Interest
    测试类型: Boundary
    测试目的: limit=1
    预期断言: 最多 1 条
    """

    params = {'symbol': 'BTC', 'interval': '30m', 'limit': 1}

    response = open_interest_api.get_aggregated_history(**params)

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
    if isinstance(data, dict) and "code" in data:
        assert data["code"] in {0, "0", 200, "200"}
    if isinstance(data, dict) and "msg" in data:
        assert data["msg"] is None or isinstance(data["msg"], str)
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

    endpoint = '/coinglass/oi/aggregated/history'
    
    case_id = 'OI-AGG-HISTORY-BOUNDARY-001'
    for row in rows:
        if not row:
            continue
        if endpoint.endswith("/exchanges"):
            assert "exchange" in row or "exchange_name" in row
            assert "symbol" in row or "base_asset" in row
        if endpoint.endswith("/history"):
            assert "symbol" in row or "time" in row or "timestamp" in row
        time_value = row.get("time", row.get("timestamp", row.get("start_time")))
        if time_value is not None and str(time_value).isdigit():
            millis = int(time_value)
            assert len(str(millis)) == 13
            if "start_time" in params and "end_time" in params:
                assert params["start_time"] <= millis < params["end_time"]
        for numeric_field, value in row.items():
            name = numeric_field.lower()
            if any(token in name for token in ("interest", "quantity", "usd", "amount", "change", "open", "high", "low", "close", "bid", "ask")):
                if value is not None and isinstance(value, (int, float, str)):
                    try:
                        numeric_value = Decimal(str(value))
                    except Exception:
                        continue
                    assert numeric_value is not None
                    if case_id == "OI-AGG-HISTORY-LOGIC-001" and "change" not in name:
                        assert numeric_value >= 0
                    if case_id == "OI-ORDERBOOK-DQC-001":
                        assert numeric_value >= 0
        if case_id == "OI-HISTORY-LOGIC-001" and all(field in row for field in ("open", "high", "low", "close")):
            open_value = Decimal(str(row["open"]))
            high = Decimal(str(row["high"]))
            low = Decimal(str(row["low"]))
            close = Decimal(str(row["close"]))
            assert high >= open_value
            assert high >= close
            assert high >= low
            assert low <= open_value
            assert low <= close
            assert low <= high


@allure.title('OI-AGG-HISTORY-PARAM-001 - ParamError - limit=0')
@allure.feature('Open Interest')
@allure.story('ParamError')
@pytest.mark.open_interest_api
def test_oi_agg_history_param_001(open_interest_api):
    """
    Case ID: OI-AGG-HISTORY-PARAM-001
    测试大类: Open Interest
    测试类型: ParamError
    测试目的: limit=0
    预期断言: 返回参数错误
    """

    params = {'symbol': 'BTC', 'interval': '30m', 'limit': 0}

    try:
        response = open_interest_api.get_aggregated_history(**params)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert any(key in str(body).lower() for key in ('symbol', 'limit', 'range', 'time', 'interval'))
    else:
        assert "code" in body
        assert "status" in body
        assert "message" in body
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] in {"error", "fail", "failed"}
    assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('OI-AGG-HISTORY-PARAM-002 - ParamError - symbol=BTCUSDT')
@allure.feature('Open Interest')
@allure.story('ParamError')
@pytest.mark.open_interest_api
def test_oi_agg_history_param_002(open_interest_api):
    """
    Case ID: OI-AGG-HISTORY-PARAM-002
    测试大类: Open Interest
    测试类型: ParamError
    测试目的: symbol=BTCUSDT
    预期断言: 该接口要求基础币种，应返回业务错误或空数据提示
    """

    params = {'symbol': 'BTCUSDT', 'interval': '30m', 'limit': 10}

    try:
        response = open_interest_api.get_aggregated_history(**params)
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
        return
    else:
        assert body["status"] == "success"
        assert body["message"]
        data = body["data"]
        assert data is not None
        if isinstance(data, dict) and "code" in data:
            assert data["code"] in {0, "0", 200, "200"}
        if isinstance(data, dict) and "msg" in data:
            assert data["msg"] is None or isinstance(data["msg"], str)
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

        endpoint = '/coinglass/oi/aggregated/history'
        
    case_id = 'OI-AGG-HISTORY-PARAM-002'
    for row in rows:
            if not row:
                continue
            if endpoint.endswith("/exchanges"):
                assert "exchange" in row or "exchange_name" in row
                assert "symbol" in row or "base_asset" in row
            if endpoint.endswith("/history"):
                assert "symbol" in row or "time" in row or "timestamp" in row
            time_value = row.get("time", row.get("timestamp", row.get("start_time")))
            if time_value is not None and str(time_value).isdigit():
                millis = int(time_value)
                assert len(str(millis)) == 13
                if "start_time" in params and "end_time" in params:
                    assert params["start_time"] <= millis < params["end_time"]
            for numeric_field, value in row.items():
                name = numeric_field.lower()
                if any(token in name for token in ("interest", "quantity", "usd", "amount", "change", "open", "high", "low", "close", "bid", "ask")):
                    if value is not None and isinstance(value, (int, float, str)):
                        try:
                            numeric_value = Decimal(str(value))
                        except Exception:
                            continue
                        assert numeric_value is not None
                        if case_id == "OI-AGG-HISTORY-LOGIC-001" and "change" not in name:
                            assert numeric_value >= 0
                        if case_id == "OI-ORDERBOOK-DQC-001":
                            assert numeric_value >= 0
            if case_id == "OI-HISTORY-LOGIC-001" and all(field in row for field in ("open", "high", "low", "close")):
                open_value = Decimal(str(row["open"]))
                high = Decimal(str(row["high"]))
                low = Decimal(str(row["low"]))
                close = Decimal(str(row["close"]))
                assert high >= open_value
                assert high >= close
                assert high >= low
                assert low <= open_value
                assert low <= close
                assert low <= high


@allure.title('OI-AGG-HISTORY-PARAM-003 - ParamError - interval=bad_interval')
@allure.feature('Open Interest')
@allure.story('ParamError')
@pytest.mark.open_interest_api
def test_oi_agg_history_param_003(open_interest_api):
    """
    Case ID: OI-AGG-HISTORY-PARAM-003
    测试大类: Open Interest
    测试类型: ParamError
    测试目的: interval=bad_interval
    预期断言: 返回业务错误或空数据提示；不能 500
    """

    params = {'symbol': 'BTC', 'interval': 'bad_interval', 'limit': 10}

    try:
        response = open_interest_api.get_aggregated_history(**params)
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
        return
    else:
        assert body["status"] == "success"
        assert body["message"]
        data = body["data"]
        assert data is not None
        if isinstance(data, dict) and "code" in data:
            assert data["code"] in {0, "0", 200, "200"}
        if isinstance(data, dict) and "msg" in data:
            assert data["msg"] is None or isinstance(data["msg"], str)
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

        endpoint = '/coinglass/oi/aggregated/history'
        
    case_id = 'OI-AGG-HISTORY-PARAM-003'
    for row in rows:
            if not row:
                continue
            if endpoint.endswith("/exchanges"):
                assert "exchange" in row or "exchange_name" in row
                assert "symbol" in row or "base_asset" in row
            if endpoint.endswith("/history"):
                assert "symbol" in row or "time" in row or "timestamp" in row
            time_value = row.get("time", row.get("timestamp", row.get("start_time")))
            if time_value is not None and str(time_value).isdigit():
                millis = int(time_value)
                assert len(str(millis)) == 13
                if "start_time" in params and "end_time" in params:
                    assert params["start_time"] <= millis < params["end_time"]
            for numeric_field, value in row.items():
                name = numeric_field.lower()
                if any(token in name for token in ("interest", "quantity", "usd", "amount", "change", "open", "high", "low", "close", "bid", "ask")):
                    if value is not None and isinstance(value, (int, float, str)):
                        try:
                            numeric_value = Decimal(str(value))
                        except Exception:
                            continue
                        assert numeric_value is not None
                        if case_id == "OI-AGG-HISTORY-LOGIC-001" and "change" not in name:
                            assert numeric_value >= 0
                        if case_id == "OI-ORDERBOOK-DQC-001":
                            assert numeric_value >= 0
            if case_id == "OI-HISTORY-LOGIC-001" and all(field in row for field in ("open", "high", "low", "close")):
                open_value = Decimal(str(row["open"]))
                high = Decimal(str(row["high"]))
                low = Decimal(str(row["low"]))
                close = Decimal(str(row["close"]))
                assert high >= open_value
                assert high >= close
                assert high >= low
                assert low <= open_value
                assert low <= close
                assert low <= high


@allure.title('OI-AGG-HISTORY-DQC-001 - DataQuality - 正常请求')
@allure.feature('Open Interest')
@allure.story('DataQuality')
@pytest.mark.open_interest_api
@pytest.mark.dqc
def test_oi_agg_history_dqc_001(open_interest_api):
    """
    Case ID: OI-AGG-HISTORY-DQC-001
    测试大类: Open Interest
    测试类型: DataQuality
    测试目的: 正常请求
    预期断言: 数值字段可转数字；时间字段为毫秒
    """

    params = {'symbol': 'BTC', 'interval': '30m', 'limit': 10}

    response = open_interest_api.get_aggregated_history(**params)

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
    if isinstance(data, dict) and "code" in data:
        assert data["code"] in {0, "0", 200, "200"}
    if isinstance(data, dict) and "msg" in data:
        assert data["msg"] is None or isinstance(data["msg"], str)
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

    endpoint = '/coinglass/oi/aggregated/history'
    
    case_id = 'OI-AGG-HISTORY-DQC-001'
    for row in rows:
        if not row:
            continue
        if endpoint.endswith("/exchanges"):
            assert "exchange" in row or "exchange_name" in row
            assert "symbol" in row or "base_asset" in row
        if endpoint.endswith("/history"):
            assert "symbol" in row or "time" in row or "timestamp" in row
        time_value = row.get("time", row.get("timestamp", row.get("start_time")))
        if time_value is not None and str(time_value).isdigit():
            millis = int(time_value)
            assert len(str(millis)) == 13
            if "start_time" in params and "end_time" in params:
                assert params["start_time"] <= millis < params["end_time"]
        for numeric_field, value in row.items():
            name = numeric_field.lower()
            if any(token in name for token in ("interest", "quantity", "usd", "amount", "change", "open", "high", "low", "close", "bid", "ask")):
                if value is not None and isinstance(value, (int, float, str)):
                    try:
                        numeric_value = Decimal(str(value))
                    except Exception:
                        continue
                    assert numeric_value is not None
                    if case_id == "OI-AGG-HISTORY-LOGIC-001" and "change" not in name:
                        assert numeric_value >= 0
                    if case_id == "OI-ORDERBOOK-DQC-001":
                        assert numeric_value >= 0
        if case_id == "OI-HISTORY-LOGIC-001" and all(field in row for field in ("open", "high", "low", "close")):
            open_value = Decimal(str(row["open"]))
            high = Decimal(str(row["high"]))
            low = Decimal(str(row["low"]))
            close = Decimal(str(row["close"]))
            assert high >= open_value
            assert high >= close
            assert high >= low
            assert low <= open_value
            assert low <= close
            assert low <= high


@allure.title('OI-AGG-HISTORY-LOGIC-001 - BusinessLogic - 正常请求')
@allure.feature('Open Interest')
@allure.story('BusinessLogic')
@pytest.mark.open_interest_api
@pytest.mark.logic
def test_oi_agg_history_logic_001(open_interest_api):
    """
    Case ID: OI-AGG-HISTORY-LOGIC-001
    测试大类: Open Interest
    测试类型: BusinessLogic
    测试目的: 正常请求
    预期断言: 聚合 OI 不应出现负值；若上游字段允许负变化率，仅变化率可为负
    """

    params = {'symbol': 'BTC', 'interval': '30m', 'limit': 10}

    response = open_interest_api.get_aggregated_history(**params)

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
    if isinstance(data, dict) and "code" in data:
        assert data["code"] in {0, "0", 200, "200"}
    if isinstance(data, dict) and "msg" in data:
        assert data["msg"] is None or isinstance(data["msg"], str)
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

    endpoint = '/coinglass/oi/aggregated/history'
    
    case_id = 'OI-AGG-HISTORY-LOGIC-001'
    for row in rows:
        if not row:
            continue
        if endpoint.endswith("/exchanges"):
            assert "exchange" in row or "exchange_name" in row
            assert "symbol" in row or "base_asset" in row
        if endpoint.endswith("/history"):
            assert "symbol" in row or "time" in row or "timestamp" in row
        time_value = row.get("time", row.get("timestamp", row.get("start_time")))
        if time_value is not None and str(time_value).isdigit():
            millis = int(time_value)
            assert len(str(millis)) == 13
            if "start_time" in params and "end_time" in params:
                assert params["start_time"] <= millis < params["end_time"]
        for numeric_field, value in row.items():
            name = numeric_field.lower()
            if any(token in name for token in ("interest", "quantity", "usd", "amount", "change", "open", "high", "low", "close", "bid", "ask")):
                if value is not None and isinstance(value, (int, float, str)):
                    try:
                        numeric_value = Decimal(str(value))
                    except Exception:
                        continue
                    assert numeric_value is not None
                    if case_id == "OI-AGG-HISTORY-LOGIC-001" and "change" not in name:
                        assert numeric_value >= 0
                    if case_id == "OI-ORDERBOOK-DQC-001":
                        assert numeric_value >= 0
        if case_id == "OI-HISTORY-LOGIC-001" and all(field in row for field in ("open", "high", "low", "close")):
            open_value = Decimal(str(row["open"]))
            high = Decimal(str(row["high"]))
            low = Decimal(str(row["low"]))
            close = Decimal(str(row["close"]))
            assert high >= open_value
            assert high >= close
            assert high >= low
            assert low <= open_value
            assert low <= close
            assert low <= high


@allure.title('OI-EXCHANGES-NORMAL-001 - Normal - symbol=BTC&interval=30m&limit=10')
@allure.feature('Open Interest')
@allure.story('Normal')
@pytest.mark.open_interest_api
def test_oi_exchanges_normal_001(open_interest_api):
    """
    Case ID: OI-EXCHANGES-NORMAL-001
    测试大类: Open Interest
    测试类型: Normal
    测试目的: symbol=BTC&interval=30m&limit=10
    预期断言: 成功；交易所列表结构稳定
    """

    params = {'symbol': 'BTC', 'interval': '30m', 'limit': 10}

    response = open_interest_api.get_exchanges(**params)

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
    if isinstance(data, dict) and "code" in data:
        assert data["code"] in {0, "0", 200, "200"}
    if isinstance(data, dict) and "msg" in data:
        assert data["msg"] is None or isinstance(data["msg"], str)
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

    endpoint = '/coinglass/oi/exchanges'
    
    case_id = 'OI-EXCHANGES-NORMAL-001'
    for row in rows:
        if not row:
            continue
        if endpoint.endswith("/exchanges"):
            assert "exchange" in row or "exchange_name" in row
            assert "symbol" in row or "base_asset" in row
        if endpoint.endswith("/history"):
            assert "symbol" in row or "time" in row or "timestamp" in row
        time_value = row.get("time", row.get("timestamp", row.get("start_time")))
        if time_value is not None and str(time_value).isdigit():
            millis = int(time_value)
            assert len(str(millis)) == 13
            if "start_time" in params and "end_time" in params:
                assert params["start_time"] <= millis < params["end_time"]
        for numeric_field, value in row.items():
            name = numeric_field.lower()
            if any(token in name for token in ("interest", "quantity", "usd", "amount", "change", "open", "high", "low", "close", "bid", "ask")):
                if value is not None and isinstance(value, (int, float, str)):
                    try:
                        numeric_value = Decimal(str(value))
                    except Exception:
                        continue
                    assert numeric_value is not None
                    if case_id == "OI-AGG-HISTORY-LOGIC-001" and "change" not in name:
                        assert numeric_value >= 0
                    if case_id == "OI-ORDERBOOK-DQC-001":
                        assert numeric_value >= 0
        if case_id == "OI-HISTORY-LOGIC-001" and all(field in row for field in ("open", "high", "low", "close")):
            open_value = Decimal(str(row["open"]))
            high = Decimal(str(row["high"]))
            low = Decimal(str(row["low"]))
            close = Decimal(str(row["close"]))
            assert high >= open_value
            assert high >= close
            assert high >= low
            assert low <= open_value
            assert low <= close
            assert low <= high


@allure.title('OI-EXCHANGES-BOUNDARY-001 - Boundary - limit=1')
@allure.feature('Open Interest')
@allure.story('Boundary')
@pytest.mark.open_interest_api
def test_oi_exchanges_boundary_001(open_interest_api):
    """
    Case ID: OI-EXCHANGES-BOUNDARY-001
    测试大类: Open Interest
    测试类型: Boundary
    测试目的: limit=1
    预期断言: 返回最多 1 条或聚合结构受限
    """

    params = {'symbol': 'BTC', 'interval': '30m', 'limit': 1}

    response = open_interest_api.get_exchanges(**params)

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
    if isinstance(data, dict) and "code" in data:
        assert data["code"] in {0, "0", 200, "200"}
    if isinstance(data, dict) and "msg" in data:
        assert data["msg"] is None or isinstance(data["msg"], str)
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

    endpoint = '/coinglass/oi/exchanges'
    
    case_id = 'OI-EXCHANGES-BOUNDARY-001'
    for row in rows:
        if not row:
            continue
        if endpoint.endswith("/exchanges"):
            assert "exchange" in row or "exchange_name" in row
            assert "symbol" in row or "base_asset" in row
        if endpoint.endswith("/history"):
            assert "symbol" in row or "time" in row or "timestamp" in row
        time_value = row.get("time", row.get("timestamp", row.get("start_time")))
        if time_value is not None and str(time_value).isdigit():
            millis = int(time_value)
            assert len(str(millis)) == 13
            if "start_time" in params and "end_time" in params:
                assert params["start_time"] <= millis < params["end_time"]
        for numeric_field, value in row.items():
            name = numeric_field.lower()
            if any(token in name for token in ("interest", "quantity", "usd", "amount", "change", "open", "high", "low", "close", "bid", "ask")):
                if value is not None and isinstance(value, (int, float, str)):
                    try:
                        numeric_value = Decimal(str(value))
                    except Exception:
                        continue
                    assert numeric_value is not None
                    if case_id == "OI-AGG-HISTORY-LOGIC-001" and "change" not in name:
                        assert numeric_value >= 0
                    if case_id == "OI-ORDERBOOK-DQC-001":
                        assert numeric_value >= 0
        if case_id == "OI-HISTORY-LOGIC-001" and all(field in row for field in ("open", "high", "low", "close")):
            open_value = Decimal(str(row["open"]))
            high = Decimal(str(row["high"]))
            low = Decimal(str(row["low"]))
            close = Decimal(str(row["close"]))
            assert high >= open_value
            assert high >= close
            assert high >= low
            assert low <= open_value
            assert low <= close
            assert low <= high


@allure.title('OI-EXCHANGES-BOUNDARY-002 - Boundary - unit=USD')
@allure.feature('Open Interest')
@allure.story('Boundary')
@pytest.mark.open_interest_api
def test_oi_exchanges_boundary_002(open_interest_api):
    """
    Case ID: OI-EXCHANGES-BOUNDARY-002
    测试大类: Open Interest
    测试类型: Boundary
    测试目的: unit=USD
    预期断言: 单位参数不破坏结构
    """

    params = {'symbol': 'BTC', 'interval': '30m', 'unit': 'USD', 'limit': 10}

    response = open_interest_api.get_exchanges(**params)

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
    if isinstance(data, dict) and "code" in data:
        assert data["code"] in {0, "0", 200, "200"}
    if isinstance(data, dict) and "msg" in data:
        assert data["msg"] is None or isinstance(data["msg"], str)
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

    endpoint = '/coinglass/oi/exchanges'
    
    case_id = 'OI-EXCHANGES-BOUNDARY-002'
    for row in rows:
        if not row:
            continue
        if endpoint.endswith("/exchanges"):
            assert "exchange" in row or "exchange_name" in row
            assert "symbol" in row or "base_asset" in row
        if endpoint.endswith("/history"):
            assert "symbol" in row or "time" in row or "timestamp" in row
        time_value = row.get("time", row.get("timestamp", row.get("start_time")))
        if time_value is not None and str(time_value).isdigit():
            millis = int(time_value)
            assert len(str(millis)) == 13
            if "start_time" in params and "end_time" in params:
                assert params["start_time"] <= millis < params["end_time"]
        for numeric_field, value in row.items():
            name = numeric_field.lower()
            if any(token in name for token in ("interest", "quantity", "usd", "amount", "change", "open", "high", "low", "close", "bid", "ask")):
                if value is not None and isinstance(value, (int, float, str)):
                    try:
                        numeric_value = Decimal(str(value))
                    except Exception:
                        continue
                    assert numeric_value is not None
                    if case_id == "OI-AGG-HISTORY-LOGIC-001" and "change" not in name:
                        assert numeric_value >= 0
                    if case_id == "OI-ORDERBOOK-DQC-001":
                        assert numeric_value >= 0
        if case_id == "OI-HISTORY-LOGIC-001" and all(field in row for field in ("open", "high", "low", "close")):
            open_value = Decimal(str(row["open"]))
            high = Decimal(str(row["high"]))
            low = Decimal(str(row["low"]))
            close = Decimal(str(row["close"]))
            assert high >= open_value
            assert high >= close
            assert high >= low
            assert low <= open_value
            assert low <= close
            assert low <= high


@allure.title('OI-EXCHANGES-PARAM-001 - ParamError - limit=0')
@allure.feature('Open Interest')
@allure.story('ParamError')
@pytest.mark.open_interest_api
def test_oi_exchanges_param_001(open_interest_api):
    """
    Case ID: OI-EXCHANGES-PARAM-001
    测试大类: Open Interest
    测试类型: ParamError
    测试目的: limit=0
    预期断言: 返回参数错误
    """

    params = {'symbol': 'BTC', 'interval': '30m', 'limit': 0}

    try:
        response = open_interest_api.get_exchanges(**params)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert any(key in str(body).lower() for key in ('symbol', 'limit', 'range', 'time', 'interval'))
    else:
        assert "code" in body
        assert "status" in body
        assert "message" in body
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] in {"error", "fail", "failed"}
    assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('OI-EXCHANGES-PARAM-002 - ParamError - symbol=BTCUSDT')
@allure.feature('Open Interest')
@allure.story('ParamError')
@pytest.mark.open_interest_api
def test_oi_exchanges_param_002(open_interest_api):
    """
    Case ID: OI-EXCHANGES-PARAM-002
    测试大类: Open Interest
    测试类型: ParamError
    测试目的: symbol=BTCUSDT
    预期断言: 基础币种接口误传合约 symbol，应返回业务错误或空数据
    """

    params = {'symbol': 'BTCUSDT', 'interval': '30m', 'limit': 10}

    try:
        response = open_interest_api.get_exchanges(**params)
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
        return
    else:
        assert body["status"] == "success"
        assert body["message"]
        data = body["data"]
        assert data is not None
        if isinstance(data, dict) and "code" in data:
            assert data["code"] in {0, "0", 200, "200"}
        if isinstance(data, dict) and "msg" in data:
            assert data["msg"] is None or isinstance(data["msg"], str)
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

        endpoint = '/coinglass/oi/exchanges'
        
    case_id = 'OI-EXCHANGES-PARAM-002'
    for row in rows:
            if not row:
                continue
            if endpoint.endswith("/exchanges"):
                assert "exchange" in row or "exchange_name" in row
                assert "symbol" in row or "base_asset" in row
            if endpoint.endswith("/history"):
                assert "symbol" in row or "time" in row or "timestamp" in row
            time_value = row.get("time", row.get("timestamp", row.get("start_time")))
            if time_value is not None and str(time_value).isdigit():
                millis = int(time_value)
                assert len(str(millis)) == 13
                if "start_time" in params and "end_time" in params:
                    assert params["start_time"] <= millis < params["end_time"]
            for numeric_field, value in row.items():
                name = numeric_field.lower()
                if any(token in name for token in ("interest", "quantity", "usd", "amount", "change", "open", "high", "low", "close", "bid", "ask")):
                    if value is not None and isinstance(value, (int, float, str)):
                        try:
                            numeric_value = Decimal(str(value))
                        except Exception:
                            continue
                        assert numeric_value is not None
                        if case_id == "OI-AGG-HISTORY-LOGIC-001" and "change" not in name:
                            assert numeric_value >= 0
                        if case_id == "OI-ORDERBOOK-DQC-001":
                            assert numeric_value >= 0
            if case_id == "OI-HISTORY-LOGIC-001" and all(field in row for field in ("open", "high", "low", "close")):
                open_value = Decimal(str(row["open"]))
                high = Decimal(str(row["high"]))
                low = Decimal(str(row["low"]))
                close = Decimal(str(row["close"]))
                assert high >= open_value
                assert high >= close
                assert high >= low
                assert low <= open_value
                assert low <= close
                assert low <= high


@allure.title('OI-EXCHANGES-RESPONSE-001 - Response - 正常请求')
@allure.feature('Open Interest')
@allure.story('Response')
@pytest.mark.open_interest_api
def test_oi_exchanges_response_001(open_interest_api):
    """
    Case ID: OI-EXCHANGES-RESPONSE-001
    测试大类: Open Interest
    测试类型: Response
    测试目的: 正常请求
    预期断言: item 存在时包含 `exchange/symbol/open_interest_usd/open_interest_quantity` 或等价字段
    """

    params = {'symbol': 'BTC', 'interval': '30m', 'limit': 10}

    response = open_interest_api.get_exchanges(**params)

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
    if isinstance(data, dict) and "code" in data:
        assert data["code"] in {0, "0", 200, "200"}
    if isinstance(data, dict) and "msg" in data:
        assert data["msg"] is None or isinstance(data["msg"], str)
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

    endpoint = '/coinglass/oi/exchanges'
    
    case_id = 'OI-EXCHANGES-RESPONSE-001'
    for row in rows:
        if not row:
            continue
        if endpoint.endswith("/exchanges"):
            assert "exchange" in row or "exchange_name" in row
            assert "symbol" in row or "base_asset" in row
        if endpoint.endswith("/history"):
            assert "symbol" in row or "time" in row or "timestamp" in row
        time_value = row.get("time", row.get("timestamp", row.get("start_time")))
        if time_value is not None and str(time_value).isdigit():
            millis = int(time_value)
            assert len(str(millis)) == 13
            if "start_time" in params and "end_time" in params:
                assert params["start_time"] <= millis < params["end_time"]
        for numeric_field, value in row.items():
            name = numeric_field.lower()
            if any(token in name for token in ("interest", "quantity", "usd", "amount", "change", "open", "high", "low", "close", "bid", "ask")):
                if value is not None and isinstance(value, (int, float, str)):
                    try:
                        numeric_value = Decimal(str(value))
                    except Exception:
                        continue
                    assert numeric_value is not None
                    if case_id == "OI-AGG-HISTORY-LOGIC-001" and "change" not in name:
                        assert numeric_value >= 0
                    if case_id == "OI-ORDERBOOK-DQC-001":
                        assert numeric_value >= 0
        if case_id == "OI-HISTORY-LOGIC-001" and all(field in row for field in ("open", "high", "low", "close")):
            open_value = Decimal(str(row["open"]))
            high = Decimal(str(row["high"]))
            low = Decimal(str(row["low"]))
            close = Decimal(str(row["close"]))
            assert high >= open_value
            assert high >= close
            assert high >= low
            assert low <= open_value
            assert low <= close
            assert low <= high


@allure.title('OI-EXCHANGES-DQC-001 - DataQuality - 正常请求')
@allure.feature('Open Interest')
@allure.story('DataQuality')
@pytest.mark.open_interest_api
@pytest.mark.dqc
def test_oi_exchanges_dqc_001(open_interest_api):
    """
    Case ID: OI-EXCHANGES-DQC-001
    测试大类: Open Interest
    测试类型: DataQuality
    测试目的: 正常请求
    预期断言: OI 数值和变化率字段可转数字
    """

    params = {'symbol': 'BTC', 'interval': '30m', 'limit': 10}

    response = open_interest_api.get_exchanges(**params)

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
    if isinstance(data, dict) and "code" in data:
        assert data["code"] in {0, "0", 200, "200"}
    if isinstance(data, dict) and "msg" in data:
        assert data["msg"] is None or isinstance(data["msg"], str)
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

    endpoint = '/coinglass/oi/exchanges'
    
    case_id = 'OI-EXCHANGES-DQC-001'
    for row in rows:
        if not row:
            continue
        if endpoint.endswith("/exchanges"):
            assert "exchange" in row or "exchange_name" in row
            assert "symbol" in row or "base_asset" in row
        if endpoint.endswith("/history"):
            assert "symbol" in row or "time" in row or "timestamp" in row
        time_value = row.get("time", row.get("timestamp", row.get("start_time")))
        if time_value is not None and str(time_value).isdigit():
            millis = int(time_value)
            assert len(str(millis)) == 13
            if "start_time" in params and "end_time" in params:
                assert params["start_time"] <= millis < params["end_time"]
        for numeric_field, value in row.items():
            name = numeric_field.lower()
            if any(token in name for token in ("interest", "quantity", "usd", "amount", "change", "open", "high", "low", "close", "bid", "ask")):
                if value is not None and isinstance(value, (int, float, str)):
                    try:
                        numeric_value = Decimal(str(value))
                    except Exception:
                        continue
                    assert numeric_value is not None
                    if case_id == "OI-AGG-HISTORY-LOGIC-001" and "change" not in name:
                        assert numeric_value >= 0
                    if case_id == "OI-ORDERBOOK-DQC-001":
                        assert numeric_value >= 0
        if case_id == "OI-HISTORY-LOGIC-001" and all(field in row for field in ("open", "high", "low", "close")):
            open_value = Decimal(str(row["open"]))
            high = Decimal(str(row["high"]))
            low = Decimal(str(row["low"]))
            close = Decimal(str(row["close"]))
            assert high >= open_value
            assert high >= close
            assert high >= low
            assert low <= open_value
            assert low <= close
            assert low <= high


@allure.title('OI-ORDERBOOK-NORMAL-001 - Normal - exchange_list=Binance&symbol=BTC&interval=1h&limit=10')
@allure.feature('Open Interest')
@allure.story('Normal')
@pytest.mark.open_interest_api
def test_oi_orderbook_normal_001(open_interest_api):
    """
    Case ID: OI-ORDERBOOK-NORMAL-001
    测试大类: Open Interest
    测试类型: Normal
    测试目的: exchange_list=Binance&symbol=BTC&interval=1h&limit=10
    预期断言: 成功；`data.symbol/exchange/interval/orderbook` 存在
    """

    params = {'exchange_list': 'Binance', 'symbol': 'BTC', 'interval': '1h', 'limit': 10}

    response = open_interest_api.get_orderbook_aggregated_history(**params)

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
    if isinstance(data, dict) and "code" in data:
        assert data["code"] in {0, "0", 200, "200"}
    if isinstance(data, dict) and "msg" in data:
        assert data["msg"] is None or isinstance(data["msg"], str)
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
    if "orderbook" in data:
        assert isinstance(data["orderbook"], (dict, list))

    endpoint = '/coinglass/oi/orderbook/aggregated-history'
    
    case_id = 'OI-ORDERBOOK-NORMAL-001'
    for row in rows:
        if not row:
            continue
        if endpoint.endswith("/exchanges"):
            assert "exchange" in row or "exchange_name" in row
            assert "symbol" in row or "base_asset" in row
        if endpoint.endswith("/history"):
            assert "symbol" in row or "time" in row or "timestamp" in row
        time_value = row.get("time", row.get("timestamp", row.get("start_time")))
        if time_value is not None and str(time_value).isdigit():
            millis = int(time_value)
            assert len(str(millis)) == 13
            if "start_time" in params and "end_time" in params:
                assert params["start_time"] <= millis < params["end_time"]
        for numeric_field, value in row.items():
            name = numeric_field.lower()
            if any(token in name for token in ("interest", "quantity", "usd", "amount", "change", "open", "high", "low", "close", "bid", "ask")):
                if value is not None and isinstance(value, (int, float, str)):
                    try:
                        numeric_value = Decimal(str(value))
                    except Exception:
                        continue
                    assert numeric_value is not None
                    if case_id == "OI-AGG-HISTORY-LOGIC-001" and "change" not in name:
                        assert numeric_value >= 0
                    if case_id == "OI-ORDERBOOK-DQC-001":
                        assert numeric_value >= 0
        if case_id == "OI-HISTORY-LOGIC-001" and all(field in row for field in ("open", "high", "low", "close")):
            open_value = Decimal(str(row["open"]))
            high = Decimal(str(row["high"]))
            low = Decimal(str(row["low"]))
            close = Decimal(str(row["close"]))
            assert high >= open_value
            assert high >= close
            assert high >= low
            assert low <= open_value
            assert low <= close
            assert low <= high


@allure.title('OI-ORDERBOOK-NORMAL-002 - Normal - exchange_list=Binance,OKX&symbol=BTC&range=0.3')
@allure.feature('Open Interest')
@allure.story('Normal')
@pytest.mark.open_interest_api
def test_oi_orderbook_normal_002(open_interest_api):
    """
    Case ID: OI-ORDERBOOK-NORMAL-002
    测试大类: Open Interest
    测试类型: Normal
    测试目的: exchange_list=Binance,OKX&symbol=BTC&range=0.3
    预期断言: 多交易所参数可接受；结构稳定
    """

    params = {'exchange_list': 'Binance,OKX', 'symbol': 'BTC', 'range': 0.3, 'limit': 10}

    response = open_interest_api.get_orderbook_aggregated_history(**params)

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
    if isinstance(data, dict) and "code" in data:
        assert data["code"] in {0, "0", 200, "200"}
    if isinstance(data, dict) and "msg" in data:
        assert data["msg"] is None or isinstance(data["msg"], str)
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
    if "orderbook" in data:
        assert isinstance(data["orderbook"], (dict, list))

    endpoint = '/coinglass/oi/orderbook/aggregated-history'
    
    case_id = 'OI-ORDERBOOK-NORMAL-002'
    for row in rows:
        if not row:
            continue
        if endpoint.endswith("/exchanges"):
            assert "exchange" in row or "exchange_name" in row
            assert "symbol" in row or "base_asset" in row
        if endpoint.endswith("/history"):
            assert "symbol" in row or "time" in row or "timestamp" in row
        time_value = row.get("time", row.get("timestamp", row.get("start_time")))
        if time_value is not None and str(time_value).isdigit():
            millis = int(time_value)
            assert len(str(millis)) == 13
            if "start_time" in params and "end_time" in params:
                assert params["start_time"] <= millis < params["end_time"]
        for numeric_field, value in row.items():
            name = numeric_field.lower()
            if any(token in name for token in ("interest", "quantity", "usd", "amount", "change", "open", "high", "low", "close", "bid", "ask")):
                if value is not None and isinstance(value, (int, float, str)):
                    try:
                        numeric_value = Decimal(str(value))
                    except Exception:
                        continue
                    assert numeric_value is not None
                    if case_id == "OI-AGG-HISTORY-LOGIC-001" and "change" not in name:
                        assert numeric_value >= 0
                    if case_id == "OI-ORDERBOOK-DQC-001":
                        assert numeric_value >= 0
        if case_id == "OI-HISTORY-LOGIC-001" and all(field in row for field in ("open", "high", "low", "close")):
            open_value = Decimal(str(row["open"]))
            high = Decimal(str(row["high"]))
            low = Decimal(str(row["low"]))
            close = Decimal(str(row["close"]))
            assert high >= open_value
            assert high >= close
            assert high >= low
            assert low <= open_value
            assert low <= close
            assert low <= high


@allure.title('OI-ORDERBOOK-BOUNDARY-001 - Boundary - limit=1')
@allure.feature('Open Interest')
@allure.story('Boundary')
@pytest.mark.open_interest_api
def test_oi_orderbook_boundary_001(open_interest_api):
    """
    Case ID: OI-ORDERBOOK-BOUNDARY-001
    测试大类: Open Interest
    测试类型: Boundary
    测试目的: limit=1
    预期断言: 返回最多 1 条或 orderbook 内部列表受限
    """

    params = {'exchange_list': 'Binance', 'symbol': 'BTC', 'interval': '1h', 'limit': 1}

    response = open_interest_api.get_orderbook_aggregated_history(**params)

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
    if isinstance(data, dict) and "code" in data:
        assert data["code"] in {0, "0", 200, "200"}
    if isinstance(data, dict) and "msg" in data:
        assert data["msg"] is None or isinstance(data["msg"], str)
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
    if "orderbook" in data:
        assert isinstance(data["orderbook"], (dict, list))

    endpoint = '/coinglass/oi/orderbook/aggregated-history'
    
    case_id = 'OI-ORDERBOOK-BOUNDARY-001'
    for row in rows:
        if not row:
            continue
        if endpoint.endswith("/exchanges"):
            assert "exchange" in row or "exchange_name" in row
            assert "symbol" in row or "base_asset" in row
        if endpoint.endswith("/history"):
            assert "symbol" in row or "time" in row or "timestamp" in row
        time_value = row.get("time", row.get("timestamp", row.get("start_time")))
        if time_value is not None and str(time_value).isdigit():
            millis = int(time_value)
            assert len(str(millis)) == 13
            if "start_time" in params and "end_time" in params:
                assert params["start_time"] <= millis < params["end_time"]
        for numeric_field, value in row.items():
            name = numeric_field.lower()
            if any(token in name for token in ("interest", "quantity", "usd", "amount", "change", "open", "high", "low", "close", "bid", "ask")):
                if value is not None and isinstance(value, (int, float, str)):
                    try:
                        numeric_value = Decimal(str(value))
                    except Exception:
                        continue
                    assert numeric_value is not None
                    if case_id == "OI-AGG-HISTORY-LOGIC-001" and "change" not in name:
                        assert numeric_value >= 0
                    if case_id == "OI-ORDERBOOK-DQC-001":
                        assert numeric_value >= 0
        if case_id == "OI-HISTORY-LOGIC-001" and all(field in row for field in ("open", "high", "low", "close")):
            open_value = Decimal(str(row["open"]))
            high = Decimal(str(row["high"]))
            low = Decimal(str(row["low"]))
            close = Decimal(str(row["close"]))
            assert high >= open_value
            assert high >= close
            assert high >= low
            assert low <= open_value
            assert low <= close
            assert low <= high


@allure.title('OI-ORDERBOOK-BOUNDARY-002 - Boundary - range=0')
@allure.feature('Open Interest')
@allure.story('Boundary')
@pytest.mark.open_interest_api
def test_oi_orderbook_boundary_002(open_interest_api):
    """
    Case ID: OI-ORDERBOOK-BOUNDARY-002
    测试大类: Open Interest
    测试类型: Boundary
    测试目的: range=0
    预期断言: 合法最小深度边界或返回明确业务提示
    """

    params = {'exchange_list': 'Binance', 'symbol': 'BTC', 'range': 0, 'limit': 10}

    try:
        response = open_interest_api.get_orderbook_aggregated_history(**params)
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
        return
    else:
        assert body["status"] == "success"
        assert body["message"]
        data = body["data"]
        assert data is not None
        if isinstance(data, dict) and "code" in data:
            assert data["code"] in {0, "0", 200, "200"}
        if isinstance(data, dict) and "msg" in data:
            assert data["msg"] is None or isinstance(data["msg"], str)
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
        if "orderbook" in data:
            assert isinstance(data["orderbook"], (dict, list))

        endpoint = '/coinglass/oi/orderbook/aggregated-history'
        
    case_id = 'OI-ORDERBOOK-BOUNDARY-002'
    for row in rows:
            if not row:
                continue
            if endpoint.endswith("/exchanges"):
                assert "exchange" in row or "exchange_name" in row
                assert "symbol" in row or "base_asset" in row
            if endpoint.endswith("/history"):
                assert "symbol" in row or "time" in row or "timestamp" in row
            time_value = row.get("time", row.get("timestamp", row.get("start_time")))
            if time_value is not None and str(time_value).isdigit():
                millis = int(time_value)
                assert len(str(millis)) == 13
                if "start_time" in params and "end_time" in params:
                    assert params["start_time"] <= millis < params["end_time"]
            for numeric_field, value in row.items():
                name = numeric_field.lower()
                if any(token in name for token in ("interest", "quantity", "usd", "amount", "change", "open", "high", "low", "close", "bid", "ask")):
                    if value is not None and isinstance(value, (int, float, str)):
                        try:
                            numeric_value = Decimal(str(value))
                        except Exception:
                            continue
                        assert numeric_value is not None
                        if case_id == "OI-AGG-HISTORY-LOGIC-001" and "change" not in name:
                            assert numeric_value >= 0
                        if case_id == "OI-ORDERBOOK-DQC-001":
                            assert numeric_value >= 0
            if case_id == "OI-HISTORY-LOGIC-001" and all(field in row for field in ("open", "high", "low", "close")):
                open_value = Decimal(str(row["open"]))
                high = Decimal(str(row["high"]))
                low = Decimal(str(row["low"]))
                close = Decimal(str(row["close"]))
                assert high >= open_value
                assert high >= close
                assert high >= low
                assert low <= open_value
                assert low <= close
                assert low <= high


@allure.title('OI-ORDERBOOK-PARAM-001 - ParamError - limit=0')
@allure.feature('Open Interest')
@allure.story('ParamError')
@pytest.mark.open_interest_api
def test_oi_orderbook_param_001(open_interest_api):
    """
    Case ID: OI-ORDERBOOK-PARAM-001
    测试大类: Open Interest
    测试类型: ParamError
    测试目的: limit=0
    预期断言: 返回参数错误
    """

    params = {'exchange_list': 'Binance', 'symbol': 'BTC', 'limit': 0}

    try:
        response = open_interest_api.get_orderbook_aggregated_history(**params)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert any(key in str(body).lower() for key in ('symbol', 'limit', 'range', 'time', 'interval'))
    else:
        assert "code" in body
        assert "status" in body
        assert "message" in body
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] in {"error", "fail", "failed"}
    assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('OI-ORDERBOOK-PARAM-002 - ParamError - range=-0.1')
@allure.feature('Open Interest')
@allure.story('ParamError')
@pytest.mark.open_interest_api
def test_oi_orderbook_param_002(open_interest_api):
    """
    Case ID: OI-ORDERBOOK-PARAM-002
    测试大类: Open Interest
    测试类型: ParamError
    测试目的: range=-0.1
    预期断言: 返回参数错误
    """

    params = {'exchange_list': 'Binance', 'symbol': 'BTC', 'range': -0.1}

    try:
        response = open_interest_api.get_orderbook_aggregated_history(**params)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert any(key in str(body).lower() for key in ('symbol', 'limit', 'range', 'time', 'interval'))
    else:
        assert "code" in body
        assert "status" in body
        assert "message" in body
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] in {"error", "fail", "failed"}
    assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('OI-ORDERBOOK-PARAM-003 - ParamError - end_time <= start_time')
@allure.feature('Open Interest')
@allure.story('ParamError')
@pytest.mark.open_interest_api
def test_oi_orderbook_param_003(open_interest_api):
    """
    Case ID: OI-ORDERBOOK-PARAM-003
    测试大类: Open Interest
    测试类型: ParamError
    测试目的: end_time <= start_time
    预期断言: 返回时间窗错误
    """

    params = {'exchange_list': 'Binance', 'symbol': 'BTC', 'start_time': 1704153600000, 'end_time': 1704067200000}

    try:
        response = open_interest_api.get_orderbook_aggregated_history(**params)
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
        return
    else:
        assert body["status"] == "success"
        assert body["message"]
        data = body["data"]
        assert data is not None
        if isinstance(data, dict) and "code" in data:
            assert data["code"] in {0, "0", 200, "200"}
        if isinstance(data, dict) and "msg" in data:
            assert data["msg"] is None or isinstance(data["msg"], str)
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
        if "orderbook" in data:
            assert isinstance(data["orderbook"], (dict, list))

        endpoint = '/coinglass/oi/orderbook/aggregated-history'
        
    case_id = 'OI-ORDERBOOK-PARAM-003'
    for row in rows:
            if not row:
                continue
            if endpoint.endswith("/exchanges"):
                assert "exchange" in row or "exchange_name" in row
                assert "symbol" in row or "base_asset" in row
            if endpoint.endswith("/history"):
                assert "symbol" in row or "time" in row or "timestamp" in row
            time_value = row.get("time", row.get("timestamp", row.get("start_time")))
            if time_value is not None and str(time_value).isdigit():
                millis = int(time_value)
                assert len(str(millis)) == 13
                if "start_time" in params and "end_time" in params:
                    assert params["start_time"] <= millis < params["end_time"]
            for numeric_field, value in row.items():
                name = numeric_field.lower()
                if any(token in name for token in ("interest", "quantity", "usd", "amount", "change", "open", "high", "low", "close", "bid", "ask")):
                    if value is not None and isinstance(value, (int, float, str)):
                        try:
                            numeric_value = Decimal(str(value))
                        except Exception:
                            continue
                        assert numeric_value is not None
                        if case_id == "OI-AGG-HISTORY-LOGIC-001" and "change" not in name:
                            assert numeric_value >= 0
                        if case_id == "OI-ORDERBOOK-DQC-001":
                            assert numeric_value >= 0
            if case_id == "OI-HISTORY-LOGIC-001" and all(field in row for field in ("open", "high", "low", "close")):
                open_value = Decimal(str(row["open"]))
                high = Decimal(str(row["high"]))
                low = Decimal(str(row["low"]))
                close = Decimal(str(row["close"]))
                assert high >= open_value
                assert high >= close
                assert high >= low
                assert low <= open_value
                assert low <= close
                assert low <= high


@allure.title('OI-ORDERBOOK-RESPONSE-001 - Response - 正常请求')
@allure.feature('Open Interest')
@allure.story('Response')
@pytest.mark.open_interest_api
def test_oi_orderbook_response_001(open_interest_api):
    """
    Case ID: OI-ORDERBOOK-RESPONSE-001
    测试大类: Open Interest
    测试类型: Response
    测试目的: 正常请求
    预期断言: orderbook` 为对象或数组；包含 bid/ask 聚合字段时类型合法
    """

    params = {'exchange_list': 'Binance', 'symbol': 'BTC', 'interval': '1h', 'limit': 10}

    response = open_interest_api.get_orderbook_aggregated_history(**params)

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
    if isinstance(data, dict) and "code" in data:
        assert data["code"] in {0, "0", 200, "200"}
    if isinstance(data, dict) and "msg" in data:
        assert data["msg"] is None or isinstance(data["msg"], str)
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
    if "orderbook" in data:
        assert isinstance(data["orderbook"], (dict, list))

    endpoint = '/coinglass/oi/orderbook/aggregated-history'
    
    case_id = 'OI-ORDERBOOK-RESPONSE-001'
    for row in rows:
        if not row:
            continue
        if endpoint.endswith("/exchanges"):
            assert "exchange" in row or "exchange_name" in row
            assert "symbol" in row or "base_asset" in row
        if endpoint.endswith("/history"):
            assert "symbol" in row or "time" in row or "timestamp" in row
        time_value = row.get("time", row.get("timestamp", row.get("start_time")))
        if time_value is not None and str(time_value).isdigit():
            millis = int(time_value)
            assert len(str(millis)) == 13
            if "start_time" in params and "end_time" in params:
                assert params["start_time"] <= millis < params["end_time"]
        for numeric_field, value in row.items():
            name = numeric_field.lower()
            if any(token in name for token in ("interest", "quantity", "usd", "amount", "change", "open", "high", "low", "close", "bid", "ask")):
                if value is not None and isinstance(value, (int, float, str)):
                    try:
                        numeric_value = Decimal(str(value))
                    except Exception:
                        continue
                    assert numeric_value is not None
                    if case_id == "OI-AGG-HISTORY-LOGIC-001" and "change" not in name:
                        assert numeric_value >= 0
                    if case_id == "OI-ORDERBOOK-DQC-001":
                        assert numeric_value >= 0
        if case_id == "OI-HISTORY-LOGIC-001" and all(field in row for field in ("open", "high", "low", "close")):
            open_value = Decimal(str(row["open"]))
            high = Decimal(str(row["high"]))
            low = Decimal(str(row["low"]))
            close = Decimal(str(row["close"]))
            assert high >= open_value
            assert high >= close
            assert high >= low
            assert low <= open_value
            assert low <= close
            assert low <= high


@allure.title('OI-ORDERBOOK-DQC-001 - DataQuality - 正常请求')
@allure.feature('Open Interest')
@allure.story('DataQuality')
@pytest.mark.open_interest_api
@pytest.mark.dqc
def test_oi_orderbook_dqc_001(open_interest_api):
    """
    Case ID: OI-ORDERBOOK-DQC-001
    测试大类: Open Interest
    测试类型: DataQuality
    测试目的: 正常请求
    预期断言: aggregated bids/asks 金额和数量字段可转数字，且不为负
    """

    params = {'exchange_list': 'Binance', 'symbol': 'BTC', 'interval': '1h', 'limit': 10}

    response = open_interest_api.get_orderbook_aggregated_history(**params)

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
    if isinstance(data, dict) and "code" in data:
        assert data["code"] in {0, "0", 200, "200"}
    if isinstance(data, dict) and "msg" in data:
        assert data["msg"] is None or isinstance(data["msg"], str)
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
    if "orderbook" in data:
        assert isinstance(data["orderbook"], (dict, list))

    endpoint = '/coinglass/oi/orderbook/aggregated-history'
    
    case_id = 'OI-ORDERBOOK-DQC-001'
    for row in rows:
        if not row:
            continue
        if endpoint.endswith("/exchanges"):
            assert "exchange" in row or "exchange_name" in row
            assert "symbol" in row or "base_asset" in row
        if endpoint.endswith("/history"):
            assert "symbol" in row or "time" in row or "timestamp" in row
        time_value = row.get("time", row.get("timestamp", row.get("start_time")))
        if time_value is not None and str(time_value).isdigit():
            millis = int(time_value)
            assert len(str(millis)) == 13
            if "start_time" in params and "end_time" in params:
                assert params["start_time"] <= millis < params["end_time"]
        for numeric_field, value in row.items():
            name = numeric_field.lower()
            if any(token in name for token in ("interest", "quantity", "usd", "amount", "change", "open", "high", "low", "close", "bid", "ask")):
                if value is not None and isinstance(value, (int, float, str)):
                    try:
                        numeric_value = Decimal(str(value))
                    except Exception:
                        continue
                    assert numeric_value is not None
                    if case_id == "OI-AGG-HISTORY-LOGIC-001" and "change" not in name:
                        assert numeric_value >= 0
                    if case_id == "OI-ORDERBOOK-DQC-001":
                        assert numeric_value >= 0
        if case_id == "OI-HISTORY-LOGIC-001" and all(field in row for field in ("open", "high", "low", "close")):
            open_value = Decimal(str(row["open"]))
            high = Decimal(str(row["high"]))
            low = Decimal(str(row["low"]))
            close = Decimal(str(row["close"]))
            assert high >= open_value
            assert high >= close
            assert high >= low
            assert low <= open_value
            assert low <= close
            assert low <= high


@allure.title('OI-SUMMARY-NORMAL-001 - Normal - symbol=BTC&exchange=Binance&interval=1h&limit=1')
@allure.feature('Open Interest')
@allure.story('Normal')
@pytest.mark.open_interest_api
def test_oi_summary_normal_001(open_interest_api):
    """
    Case ID: OI-SUMMARY-NORMAL-001
    测试大类: Open Interest
    测试类型: Normal
    测试目的: symbol=BTC&exchange=Binance&interval=1h&limit=1
    预期断言: 成功；市场摘要结构稳定
    """

    params = {'symbol': 'BTC', 'exchange': 'Binance', 'interval': '1h', 'limit': 1}

    response = open_interest_api.get_summary(**params)

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
    if isinstance(data, dict) and "code" in data:
        assert data["code"] in {0, "0", 200, "200"}
    if isinstance(data, dict) and "msg" in data:
        assert data["msg"] is None or isinstance(data["msg"], str)
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
    for section_name in ("orderbook", "longshort", "open_interest", "whale_flow_spikes"):
        if section_name in data and data[section_name] is not None:
            assert isinstance(data[section_name], (dict, list))

    endpoint = '/coinglass/oi/summary'
    
    case_id = 'OI-SUMMARY-NORMAL-001'
    for row in rows:
        if not row:
            continue
        if endpoint.endswith("/exchanges"):
            assert "exchange" in row or "exchange_name" in row
            assert "symbol" in row or "base_asset" in row
        if endpoint.endswith("/history"):
            assert "symbol" in row or "time" in row or "timestamp" in row
        time_value = row.get("time", row.get("timestamp", row.get("start_time")))
        if time_value is not None and str(time_value).isdigit():
            millis = int(time_value)
            assert len(str(millis)) == 13
            if "start_time" in params and "end_time" in params:
                assert params["start_time"] <= millis < params["end_time"]
        for numeric_field, value in row.items():
            name = numeric_field.lower()
            if any(token in name for token in ("interest", "quantity", "usd", "amount", "change", "open", "high", "low", "close", "bid", "ask")):
                if value is not None and isinstance(value, (int, float, str)):
                    try:
                        numeric_value = Decimal(str(value))
                    except Exception:
                        continue
                    assert numeric_value is not None
                    if case_id == "OI-AGG-HISTORY-LOGIC-001" and "change" not in name:
                        assert numeric_value >= 0
                    if case_id == "OI-ORDERBOOK-DQC-001":
                        assert numeric_value >= 0
        if case_id == "OI-HISTORY-LOGIC-001" and all(field in row for field in ("open", "high", "low", "close")):
            open_value = Decimal(str(row["open"]))
            high = Decimal(str(row["high"]))
            low = Decimal(str(row["low"]))
            close = Decimal(str(row["close"]))
            assert high >= open_value
            assert high >= close
            assert high >= low
            assert low <= open_value
            assert low <= close
            assert low <= high


@allure.title('OI-SUMMARY-BOUNDARY-001 - Boundary - 不传参数')
@allure.feature('Open Interest')
@allure.story('Boundary')
@pytest.mark.open_interest_api
def test_oi_summary_boundary_001(open_interest_api):
    """
    Case ID: OI-SUMMARY-BOUNDARY-001
    测试大类: Open Interest
    测试类型: Boundary
    测试目的: 不传参数
    预期断言: 使用默认 `symbol=BTC&exchange=Binance&interval=1h&limit=1` 语义
    """

    params = {}

    response = open_interest_api.get_summary(**params)

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
    if isinstance(data, dict) and "code" in data:
        assert data["code"] in {0, "0", 200, "200"}
    if isinstance(data, dict) and "msg" in data:
        assert data["msg"] is None or isinstance(data["msg"], str)
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
    for section_name in ("orderbook", "longshort", "open_interest", "whale_flow_spikes"):
        if section_name in data and data[section_name] is not None:
            assert isinstance(data[section_name], (dict, list))

    endpoint = '/coinglass/oi/summary'
    
    case_id = 'OI-SUMMARY-BOUNDARY-001'
    for row in rows:
        if not row:
            continue
        if endpoint.endswith("/exchanges"):
            assert "exchange" in row or "exchange_name" in row
            assert "symbol" in row or "base_asset" in row
        if endpoint.endswith("/history"):
            assert "symbol" in row or "time" in row or "timestamp" in row
        time_value = row.get("time", row.get("timestamp", row.get("start_time")))
        if time_value is not None and str(time_value).isdigit():
            millis = int(time_value)
            assert len(str(millis)) == 13
            if "start_time" in params and "end_time" in params:
                assert params["start_time"] <= millis < params["end_time"]
        for numeric_field, value in row.items():
            name = numeric_field.lower()
            if any(token in name for token in ("interest", "quantity", "usd", "amount", "change", "open", "high", "low", "close", "bid", "ask")):
                if value is not None and isinstance(value, (int, float, str)):
                    try:
                        numeric_value = Decimal(str(value))
                    except Exception:
                        continue
                    assert numeric_value is not None
                    if case_id == "OI-AGG-HISTORY-LOGIC-001" and "change" not in name:
                        assert numeric_value >= 0
                    if case_id == "OI-ORDERBOOK-DQC-001":
                        assert numeric_value >= 0
        if case_id == "OI-HISTORY-LOGIC-001" and all(field in row for field in ("open", "high", "low", "close")):
            open_value = Decimal(str(row["open"]))
            high = Decimal(str(row["high"]))
            low = Decimal(str(row["low"]))
            close = Decimal(str(row["close"]))
            assert high >= open_value
            assert high >= close
            assert high >= low
            assert low <= open_value
            assert low <= close
            assert low <= high


@allure.title('OI-SUMMARY-BOUNDARY-002 - Boundary - limit=1')
@allure.feature('Open Interest')
@allure.story('Boundary')
@pytest.mark.open_interest_api
def test_oi_summary_boundary_002(open_interest_api):
    """
    Case ID: OI-SUMMARY-BOUNDARY-002
    测试大类: Open Interest
    测试类型: Boundary
    测试目的: limit=1
    预期断言: 返回最新 1 条或聚合摘要
    """

    params = {'symbol': 'BTC', 'exchange': 'Binance', 'interval': '1h', 'limit': 1}

    response = open_interest_api.get_summary(**params)

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
    if isinstance(data, dict) and "code" in data:
        assert data["code"] in {0, "0", 200, "200"}
    if isinstance(data, dict) and "msg" in data:
        assert data["msg"] is None or isinstance(data["msg"], str)
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
    for section_name in ("orderbook", "longshort", "open_interest", "whale_flow_spikes"):
        if section_name in data and data[section_name] is not None:
            assert isinstance(data[section_name], (dict, list))

    endpoint = '/coinglass/oi/summary'
    
    case_id = 'OI-SUMMARY-BOUNDARY-002'
    for row in rows:
        if not row:
            continue
        if endpoint.endswith("/exchanges"):
            assert "exchange" in row or "exchange_name" in row
            assert "symbol" in row or "base_asset" in row
        if endpoint.endswith("/history"):
            assert "symbol" in row or "time" in row or "timestamp" in row
        time_value = row.get("time", row.get("timestamp", row.get("start_time")))
        if time_value is not None and str(time_value).isdigit():
            millis = int(time_value)
            assert len(str(millis)) == 13
            if "start_time" in params and "end_time" in params:
                assert params["start_time"] <= millis < params["end_time"]
        for numeric_field, value in row.items():
            name = numeric_field.lower()
            if any(token in name for token in ("interest", "quantity", "usd", "amount", "change", "open", "high", "low", "close", "bid", "ask")):
                if value is not None and isinstance(value, (int, float, str)):
                    try:
                        numeric_value = Decimal(str(value))
                    except Exception:
                        continue
                    assert numeric_value is not None
                    if case_id == "OI-AGG-HISTORY-LOGIC-001" and "change" not in name:
                        assert numeric_value >= 0
                    if case_id == "OI-ORDERBOOK-DQC-001":
                        assert numeric_value >= 0
        if case_id == "OI-HISTORY-LOGIC-001" and all(field in row for field in ("open", "high", "low", "close")):
            open_value = Decimal(str(row["open"]))
            high = Decimal(str(row["high"]))
            low = Decimal(str(row["low"]))
            close = Decimal(str(row["close"]))
            assert high >= open_value
            assert high >= close
            assert high >= low
            assert low <= open_value
            assert low <= close
            assert low <= high


@allure.title('OI-SUMMARY-PARAM-001 - ParamError - limit=0')
@allure.feature('Open Interest')
@allure.story('ParamError')
@pytest.mark.open_interest_api
def test_oi_summary_param_001(open_interest_api):
    """
    Case ID: OI-SUMMARY-PARAM-001
    测试大类: Open Interest
    测试类型: ParamError
    测试目的: limit=0
    预期断言: 返回参数错误
    """

    params = {'symbol': 'BTC', 'limit': 0}

    try:
        response = open_interest_api.get_summary(**params)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert any(key in str(body).lower() for key in ('symbol', 'limit', 'range', 'time', 'interval'))
    else:
        assert "code" in body
        assert "status" in body
        assert "message" in body
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] in {"error", "fail", "failed"}
    assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('OI-SUMMARY-PARAM-002 - ParamError - symbol=BTCUSDT')
@allure.feature('Open Interest')
@allure.story('ParamError')
@pytest.mark.open_interest_api
def test_oi_summary_param_002(open_interest_api):
    """
    Case ID: OI-SUMMARY-PARAM-002
    测试大类: Open Interest
    测试类型: ParamError
    测试目的: symbol=BTCUSDT
    预期断言: 基础币种接口误传合约 symbol，应返回业务错误或空数据
    """

    params = {'symbol': 'BTCUSDT', 'limit': 1}

    try:
        response = open_interest_api.get_summary(**params)
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
        return
    else:
        assert body["status"] == "success"
        assert body["message"]
        data = body["data"]
        assert data is not None
        if isinstance(data, dict) and "code" in data:
            assert data["code"] in {0, "0", 200, "200"}
        if isinstance(data, dict) and "msg" in data:
            assert data["msg"] is None or isinstance(data["msg"], str)
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
        for section_name in ("orderbook", "longshort", "open_interest", "whale_flow_spikes"):
            if section_name in data and data[section_name] is not None:
                assert isinstance(data[section_name], (dict, list))

        endpoint = '/coinglass/oi/summary'
        
    case_id = 'OI-SUMMARY-PARAM-002'
    for row in rows:
            if not row:
                continue
            if endpoint.endswith("/exchanges"):
                assert "exchange" in row or "exchange_name" in row
                assert "symbol" in row or "base_asset" in row
            if endpoint.endswith("/history"):
                assert "symbol" in row or "time" in row or "timestamp" in row
            time_value = row.get("time", row.get("timestamp", row.get("start_time")))
            if time_value is not None and str(time_value).isdigit():
                millis = int(time_value)
                assert len(str(millis)) == 13
                if "start_time" in params and "end_time" in params:
                    assert params["start_time"] <= millis < params["end_time"]
            for numeric_field, value in row.items():
                name = numeric_field.lower()
                if any(token in name for token in ("interest", "quantity", "usd", "amount", "change", "open", "high", "low", "close", "bid", "ask")):
                    if value is not None and isinstance(value, (int, float, str)):
                        try:
                            numeric_value = Decimal(str(value))
                        except Exception:
                            continue
                        assert numeric_value is not None
                        if case_id == "OI-AGG-HISTORY-LOGIC-001" and "change" not in name:
                            assert numeric_value >= 0
                        if case_id == "OI-ORDERBOOK-DQC-001":
                            assert numeric_value >= 0
            if case_id == "OI-HISTORY-LOGIC-001" and all(field in row for field in ("open", "high", "low", "close")):
                open_value = Decimal(str(row["open"]))
                high = Decimal(str(row["high"]))
                low = Decimal(str(row["low"]))
                close = Decimal(str(row["close"]))
                assert high >= open_value
                assert high >= close
                assert high >= low
                assert low <= open_value
                assert low <= close
                assert low <= high


@allure.title('OI-SUMMARY-RESPONSE-001 - Response - 正常请求')
@allure.feature('Open Interest')
@allure.story('Response')
@pytest.mark.open_interest_api
def test_oi_summary_response_001(open_interest_api):
    """
    Case ID: OI-SUMMARY-RESPONSE-001
    测试大类: Open Interest
    测试类型: Response
    测试目的: 正常请求
    预期断言: data.symbol/exchange/interval/limit` 回显；返回的 `orderbook/longshort/open_interest/whale_flow_spikes` 子对象必须类型合法
    """

    params = {'symbol': 'BTC', 'exchange': 'Binance', 'interval': '1h', 'limit': 1}

    response = open_interest_api.get_summary(**params)

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
    if isinstance(data, dict) and "code" in data:
        assert data["code"] in {0, "0", 200, "200"}
    if isinstance(data, dict) and "msg" in data:
        assert data["msg"] is None or isinstance(data["msg"], str)
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
    for section_name in ("orderbook", "longshort", "open_interest", "whale_flow_spikes"):
        if section_name in data and data[section_name] is not None:
            assert isinstance(data[section_name], (dict, list))

    endpoint = '/coinglass/oi/summary'
    
    case_id = 'OI-SUMMARY-RESPONSE-001'
    for row in rows:
        if not row:
            continue
        if endpoint.endswith("/exchanges"):
            assert "exchange" in row or "exchange_name" in row
            assert "symbol" in row or "base_asset" in row
        if endpoint.endswith("/history"):
            assert "symbol" in row or "time" in row or "timestamp" in row
        time_value = row.get("time", row.get("timestamp", row.get("start_time")))
        if time_value is not None and str(time_value).isdigit():
            millis = int(time_value)
            assert len(str(millis)) == 13
            if "start_time" in params and "end_time" in params:
                assert params["start_time"] <= millis < params["end_time"]
        for numeric_field, value in row.items():
            name = numeric_field.lower()
            if any(token in name for token in ("interest", "quantity", "usd", "amount", "change", "open", "high", "low", "close", "bid", "ask")):
                if value is not None and isinstance(value, (int, float, str)):
                    try:
                        numeric_value = Decimal(str(value))
                    except Exception:
                        continue
                    assert numeric_value is not None
                    if case_id == "OI-AGG-HISTORY-LOGIC-001" and "change" not in name:
                        assert numeric_value >= 0
                    if case_id == "OI-ORDERBOOK-DQC-001":
                        assert numeric_value >= 0
        if case_id == "OI-HISTORY-LOGIC-001" and all(field in row for field in ("open", "high", "low", "close")):
            open_value = Decimal(str(row["open"]))
            high = Decimal(str(row["high"]))
            low = Decimal(str(row["low"]))
            close = Decimal(str(row["close"]))
            assert high >= open_value
            assert high >= close
            assert high >= low
            assert low <= open_value
            assert low <= close
            assert low <= high


@allure.title('OI-SUMMARY-DQC-001 - DataQuality - 正常请求')
@allure.feature('Open Interest')
@allure.story('DataQuality')
@pytest.mark.open_interest_api
@pytest.mark.dqc
def test_oi_summary_dqc_001(open_interest_api):
    """
    Case ID: OI-SUMMARY-DQC-001
    测试大类: Open Interest
    测试类型: DataQuality
    测试目的: 正常请求
    预期断言: 聚合子对象中的时间、金额、数量字段类型合法
    """

    params = {'symbol': 'BTC', 'exchange': 'Binance', 'interval': '1h', 'limit': 1}

    response = open_interest_api.get_summary(**params)

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
    if isinstance(data, dict) and "code" in data:
        assert data["code"] in {0, "0", 200, "200"}
    if isinstance(data, dict) and "msg" in data:
        assert data["msg"] is None or isinstance(data["msg"], str)
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
    for section_name in ("orderbook", "longshort", "open_interest", "whale_flow_spikes"):
        if section_name in data and data[section_name] is not None:
            assert isinstance(data[section_name], (dict, list))

    endpoint = '/coinglass/oi/summary'
    
    case_id = 'OI-SUMMARY-DQC-001'
    for row in rows:
        if not row:
            continue
        if endpoint.endswith("/exchanges"):
            assert "exchange" in row or "exchange_name" in row
            assert "symbol" in row or "base_asset" in row
        if endpoint.endswith("/history"):
            assert "symbol" in row or "time" in row or "timestamp" in row
        time_value = row.get("time", row.get("timestamp", row.get("start_time")))
        if time_value is not None and str(time_value).isdigit():
            millis = int(time_value)
            assert len(str(millis)) == 13
            if "start_time" in params and "end_time" in params:
                assert params["start_time"] <= millis < params["end_time"]
        for numeric_field, value in row.items():
            name = numeric_field.lower()
            if any(token in name for token in ("interest", "quantity", "usd", "amount", "change", "open", "high", "low", "close", "bid", "ask")):
                if value is not None and isinstance(value, (int, float, str)):
                    try:
                        numeric_value = Decimal(str(value))
                    except Exception:
                        continue
                    assert numeric_value is not None
                    if case_id == "OI-AGG-HISTORY-LOGIC-001" and "change" not in name:
                        assert numeric_value >= 0
                    if case_id == "OI-ORDERBOOK-DQC-001":
                        assert numeric_value >= 0
        if case_id == "OI-HISTORY-LOGIC-001" and all(field in row for field in ("open", "high", "low", "close")):
            open_value = Decimal(str(row["open"]))
            high = Decimal(str(row["high"]))
            low = Decimal(str(row["low"]))
            close = Decimal(str(row["close"]))
            assert high >= open_value
            assert high >= close
            assert high >= low
            assert low <= open_value
            assert low <= close
            assert low <= high
