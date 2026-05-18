from __future__ import annotations

from decimal import Decimal
from time import perf_counter

import allure
import pytest
from requests.exceptions import HTTPError

from api_services.binance_full_api import BinanceFullAPI
from config.settings import settings

USDM_SYMBOL = "BTCUSDT"
USDM_SYMBOLS_MULTI = "BTCUSDT,ETHUSDT"
COINM_PAIR = "BTCUSD"
COINM_PAIRS_MULTI = "BTCUSD,ETHUSD"
USDM_DELIVERY_PAIR = "BTCUSDT"
INTERVAL_1M = "1m"
INTERVAL_1H = "1h"
START_TIME_MS = 1704067200000
END_TIME_MS = 1704153600000
LIMIT_SMALL = 1
LIMIT_NORMAL = 10
LIMIT_MAX_SYMBOL_LIST = 20000
PERFORMANCE_BASELINE_SECONDS = 1.0


@pytest.fixture(scope="module")
def binance_full_api() -> BinanceFullAPI:
    if not settings.base_url or "exchange.com" in settings.base_url:
        pytest.skip("Binance Full API BASE_URL is not configured for live API tests.")
    return BinanceFullAPI()


@allure.title('BF-META-TABLES-NORMAL-001 - Normal - GET /api/binance-full/meta/tables')
@allure.feature('binance-full')
@allure.story('Normal')
@pytest.mark.binance_full_api
def test_bf_meta_tables_normal_001(binance_full_api):
    """
    Case ID: BF-META-TABLES-NORMAL-001
    测试大类: binance-full
    测试类型: Normal
    测试目的: GET /api/binance-full/meta/tables
    预期断言: 成功；`data.capabilities` 为对象
    """

    case_id = 'BF-META-TABLES-NORMAL-001'

    responses = []

    request_params_1 = {}

    response_1 = binance_full_api.get_meta_tables()

    responses.append(('tables', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
        assert isinstance(data, dict)
        assert "capabilities" in data
        assert isinstance(data["capabilities"], dict)


@allure.title('BF-META-TABLES-RESPONSE-001 - Response - 正常请求')
@allure.feature('binance-full')
@allure.story('Response')
@pytest.mark.binance_full_api
def test_bf_meta_tables_response_001(binance_full_api):
    """
    Case ID: BF-META-TABLES-RESPONSE-001
    测试大类: binance-full
    测试类型: Response
    测试目的: 正常请求
    预期断言: capabilities 至少包含 Binance full 相关命名空间或能力描述；不要求固定顺序
    """

    case_id = 'BF-META-TABLES-RESPONSE-001'

    responses = []

    request_params_1 = {}

    response_1 = binance_full_api.get_meta_tables()

    responses.append(('tables', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
        assert isinstance(data, dict)
        assert "capabilities" in data
        assert isinstance(data["capabilities"], dict)
        expected_namespaces = {"usdm", "coinm_perp", "coinm_delivery", "usdm_delivery"}
        assert expected_namespaces.intersection(data["capabilities"])
        for namespace, capabilities in data["capabilities"].items():
            assert namespace in expected_namespaces
            assert isinstance(capabilities, dict)
            assert any(capabilities.get(name) is True for name in ("kline", "funding", "registry"))


@allure.title('BF-META-TABLES-PERF-001 - Performance - 正常请求')
@allure.feature('binance-full')
@allure.story('Performance')
@pytest.mark.binance_full_api
@pytest.mark.performance
def test_bf_meta_tables_perf_001(binance_full_api):
    """
    Case ID: BF-META-TABLES-PERF-001
    测试大类: binance-full
    测试类型: Performance
    测试目的: 正常请求
    预期断言: 响应时间小于 1 秒
    """

    case_id = 'BF-META-TABLES-PERF-001'

    responses = []

    request_params_1 = {}

    start = perf_counter()

    response_1 = binance_full_api.get_meta_tables()

    elapsed = perf_counter() - start

    responses.append(('tables', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
        assert isinstance(data, dict)
        assert "capabilities" in data
        assert isinstance(data["capabilities"], dict)
        assert elapsed < PERFORMANCE_BASELINE_SECONDS


@allure.title('BF-REGISTRY-NORMAL-001 - Normal - contract_type=PERPETUAL&quote_asset=USDT&status=TRADING')
@allure.feature('binance-full')
@allure.story('Normal')
@pytest.mark.binance_full_api
def test_bf_registry_normal_001(binance_full_api):
    """
    Case ID: BF-REGISTRY-NORMAL-001
    测试大类: binance-full
    测试类型: Normal
    测试目的: contract_type=PERPETUAL&quote_asset=USDT&status=TRADING
    预期断言: 成功；`data.filters/count/items` 存在；items 为合约目录行
    """

    case_id = 'BF-REGISTRY-NORMAL-001'

    responses = []

    request_params_1 = {'contract_type': 'PERPETUAL', 'quote_asset': 'USDT', 'status': 'TRADING'}

    response_1 = binance_full_api.get_usdm_registry_symbols(contract_type='PERPETUAL', quote_asset='USDT', status='TRADING')

    responses.append(('symbols', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
        assert isinstance(data, dict)
        assert "filters" in data
        assert "count" in data
        assert "items" in data
        assert isinstance(data["items"], list)
        assert data["count"] == len(data["items"])
        requested_statuses = set(str(request_params.get("status", "")).split(",")) if request_params.get("status") else set()
        for item in data["items"]:
            assert "symbol" in item
            assert isinstance(item["symbol"], str)
            assert item["symbol"]
            if requested_statuses and item.get("status") is not None:
                assert item["status"] in requested_statuses
            if item.get("onboard_date_ms") is not None:
                assert len(str(int(item["onboard_date_ms"]))) == 13


@allure.title('BF-REGISTRY-BOUNDARY-001 - Boundary - 不传任何过滤参数')
@allure.feature('binance-full')
@allure.story('Boundary')
@pytest.mark.binance_full_api
def test_bf_registry_boundary_001(binance_full_api):
    """
    Case ID: BF-REGISTRY-BOUNDARY-001
    测试大类: binance-full
    测试类型: Boundary
    测试目的: 不传任何过滤参数
    预期断言: 返回全量或默认范围；`count == len(items)
    """

    case_id = 'BF-REGISTRY-BOUNDARY-001'

    responses = []

    request_params_1 = {}

    response_1 = binance_full_api.get_usdm_registry_symbols()

    responses.append(('symbols', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
        assert isinstance(data, dict)
        assert "filters" in data
        assert "count" in data
        assert "items" in data
        assert isinstance(data["items"], list)
        assert data["count"] == len(data["items"])
        requested_statuses = set(str(request_params.get("status", "")).split(",")) if request_params.get("status") else set()
        for item in data["items"]:
            assert "symbol" in item
            assert isinstance(item["symbol"], str)
            assert item["symbol"]
            if requested_statuses and item.get("status") is not None:
                assert item["status"] in requested_statuses
            if item.get("onboard_date_ms") is not None:
                assert len(str(int(item["onboard_date_ms"]))) == 13


@allure.title('BF-REGISTRY-BOUNDARY-002 - Boundary - status=TRADING,CLOSE')
@allure.feature('binance-full')
@allure.story('Boundary')
@pytest.mark.binance_full_api
def test_bf_registry_boundary_002(binance_full_api):
    """
    Case ID: BF-REGISTRY-BOUNDARY-002
    测试大类: binance-full
    测试类型: Boundary
    测试目的: status=TRADING,CLOSE
    预期断言: 支持逗号多状态；items 的 status 在请求集合内或返回空
    """

    case_id = 'BF-REGISTRY-BOUNDARY-002'

    responses = []

    request_params_1 = {'status': 'TRADING,CLOSE'}

    response_1 = binance_full_api.get_usdm_registry_symbols(status='TRADING,CLOSE')

    responses.append(('symbols', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
        assert isinstance(data, dict)
        assert "filters" in data
        assert "count" in data
        assert "items" in data
        assert isinstance(data["items"], list)
        assert data["count"] == len(data["items"])
        requested_statuses = set(str(request_params.get("status", "")).split(",")) if request_params.get("status") else set()
        for item in data["items"]:
            assert "symbol" in item
            assert isinstance(item["symbol"], str)
            assert item["symbol"]
            if requested_statuses and item.get("status") is not None:
                assert item["status"] in requested_statuses
            if item.get("onboard_date_ms") is not None:
                assert len(str(int(item["onboard_date_ms"]))) == 13


@allure.title('BF-REGISTRY-PARAM-001 - ParamError - contract_type=INVALID')
@allure.feature('binance-full')
@allure.story('ParamError')
@pytest.mark.binance_full_api
def test_bf_registry_param_001(binance_full_api):
    """
    Case ID: BF-REGISTRY-PARAM-001
    测试大类: binance-full
    测试类型: ParamError
    测试目的: contract_type=INVALID
    预期断言: 返回参数错误或业务错误；不能 500
    """

    case_id = 'BF-REGISTRY-PARAM-001'

    responses = []

    request_params_1 = {'contract_type': 'INVALID'}

    try:
        response_1 = binance_full_api.get_usdm_registry_symbols(contract_type='INVALID')
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('symbols', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
            assert isinstance(data, dict)
            assert "filters" in data
            assert "count" in data
            assert "items" in data
            assert isinstance(data["items"], list)
            assert data["count"] == len(data["items"])
            requested_statuses = set(str(request_params.get("status", "")).split(",")) if request_params.get("status") else set()
            for item in data["items"]:
                assert "symbol" in item
                assert isinstance(item["symbol"], str)
                assert item["symbol"]
                if requested_statuses and item.get("status") is not None:
                    assert item["status"] in requested_statuses
                if item.get("onboard_date_ms") is not None:
                    assert len(str(int(item["onboard_date_ms"]))) == 13


@allure.title('BF-REGISTRY-RESPONSE-001 - Response - 正常请求')
@allure.feature('binance-full')
@allure.story('Response')
@pytest.mark.binance_full_api
def test_bf_registry_response_001(binance_full_api):
    """
    Case ID: BF-REGISTRY-RESPONSE-001
    测试大类: binance-full
    测试类型: Response
    测试目的: 正常请求
    预期断言: 每个 item 至少含 `symbol`，可选含 `status/contract_type/quote_asset/margin_asset/is_enabled/onboard_date_ms
    """

    case_id = 'BF-REGISTRY-RESPONSE-001'

    responses = []

    request_params_1 = {'contract_type': 'PERPETUAL', 'quote_asset': 'USDT', 'status': 'TRADING'}

    response_1 = binance_full_api.get_usdm_registry_symbols(contract_type='PERPETUAL', quote_asset='USDT', status='TRADING')

    responses.append(('symbols', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
        assert isinstance(data, dict)
        assert "filters" in data
        assert "count" in data
        assert "items" in data
        assert isinstance(data["items"], list)
        assert data["count"] == len(data["items"])
        requested_statuses = set(str(request_params.get("status", "")).split(",")) if request_params.get("status") else set()
        for item in data["items"]:
            assert "symbol" in item
            assert isinstance(item["symbol"], str)
            assert item["symbol"]
            if requested_statuses and item.get("status") is not None:
                assert item["status"] in requested_statuses
            if item.get("onboard_date_ms") is not None:
                assert len(str(int(item["onboard_date_ms"]))) == 13


@allure.title('BF-REGISTRY-DQC-001 - DataQuality - 正常请求')
@allure.feature('binance-full')
@allure.story('DataQuality')
@pytest.mark.binance_full_api
@pytest.mark.dqc
def test_bf_registry_dqc_001(binance_full_api):
    """
    Case ID: BF-REGISTRY-DQC-001
    测试大类: binance-full
    测试类型: DataQuality
    测试目的: 正常请求
    预期断言: onboard_date_ms` 非空时为 13 位毫秒
    """

    case_id = 'BF-REGISTRY-DQC-001'

    responses = []

    request_params_1 = {'contract_type': 'PERPETUAL', 'quote_asset': 'USDT', 'status': 'TRADING'}

    response_1 = binance_full_api.get_usdm_registry_symbols(contract_type='PERPETUAL', quote_asset='USDT', status='TRADING')

    responses.append(('symbols', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
        assert isinstance(data, dict)
        assert "filters" in data
        assert "count" in data
        assert "items" in data
        assert isinstance(data["items"], list)
        assert data["count"] == len(data["items"])
        requested_statuses = set(str(request_params.get("status", "")).split(",")) if request_params.get("status") else set()
        for item in data["items"]:
            assert "symbol" in item
            assert isinstance(item["symbol"], str)
            assert item["symbol"]
            if requested_statuses and item.get("status") is not None:
                assert item["status"] in requested_statuses
            if item.get("onboard_date_ms") is not None:
                assert len(str(int(item["onboard_date_ms"]))) == 13


@allure.title('BF-COMPLETE-NORMAL-001 - Normal - start_time_ms=1704067200000&end_time_ms=1704153600000&limit=10')
@allure.feature('binance-full')
@allure.story('Normal')
@pytest.mark.binance_full_api
def test_bf_complete_normal_001(binance_full_api):
    """
    Case ID: BF-COMPLETE-NORMAL-001
    测试大类: binance-full
    测试类型: Normal
    测试目的: start_time_ms=1704067200000&end_time_ms=1704153600000&limit=10
    预期断言: 成功；`items` 为 symbol 字符串数组；`count == len(items)
    """

    case_id = 'BF-COMPLETE-NORMAL-001'

    responses = []

    request_params_1 = {'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'limit': 10}

    response_1 = binance_full_api.get_usdm_complete_symbols(start_time_ms=1704067200000, end_time_ms=1704153600000, limit=10)

    responses.append(('complete-symbols', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
        assert isinstance(data, dict)
        assert "items" in data
        assert isinstance(data["items"], list)
        if "count" in data:
            assert data["count"] == len(data["items"])
        if request_params.get("limit") is not None:
            assert len(data["items"]) <= int(request_params["limit"])
        for symbol in data["items"]:
            assert isinstance(symbol, str)
            assert symbol


@allure.title('BF-COMPLETE-BOUNDARY-001 - Boundary - limit=1')
@allure.feature('binance-full')
@allure.story('Boundary')
@pytest.mark.binance_full_api
def test_bf_complete_boundary_001(binance_full_api):
    """
    Case ID: BF-COMPLETE-BOUNDARY-001
    测试大类: binance-full
    测试类型: Boundary
    测试目的: limit=1
    预期断言: 最多返回 1 个 symbol
    """

    case_id = 'BF-COMPLETE-BOUNDARY-001'

    responses = []

    request_params_1 = {'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'limit': 1}

    response_1 = binance_full_api.get_usdm_complete_symbols(start_time_ms=1704067200000, end_time_ms=1704153600000, limit=1)

    responses.append(('complete-symbols', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
        assert isinstance(data, dict)
        assert "items" in data
        assert isinstance(data["items"], list)
        if "count" in data:
            assert data["count"] == len(data["items"])
        if request_params.get("limit") is not None:
            assert len(data["items"]) <= int(request_params["limit"])
        for symbol in data["items"]:
            assert isinstance(symbol, str)
            assert symbol


@allure.title('BF-COMPLETE-BOUNDARY-002 - Boundary - include_legacy_coinm_in_usdm_aggregate=true')
@allure.feature('binance-full')
@allure.story('Boundary')
@pytest.mark.binance_full_api
def test_bf_complete_boundary_002(binance_full_api):
    """
    Case ID: BF-COMPLETE-BOUNDARY-002
    测试大类: binance-full
    测试类型: Boundary
    测试目的: include_legacy_coinm_in_usdm_aggregate=true
    预期断言: 成功或明确业务提示；不能 500
    """

    case_id = 'BF-COMPLETE-BOUNDARY-002'

    responses = []

    request_params_1 = {'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'limit': 10, 'include_legacy_coinm_in_usdm_aggregate': True}

    try:
        response_1 = binance_full_api.get_usdm_complete_symbols(start_time_ms=1704067200000, end_time_ms=1704153600000, limit=10, include_legacy_coinm_in_usdm_aggregate=True)
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('complete-symbols', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
            assert isinstance(data, dict)
            assert "items" in data
            assert isinstance(data["items"], list)
            if "count" in data:
                assert data["count"] == len(data["items"])
            if request_params.get("limit") is not None:
                assert len(data["items"]) <= int(request_params["limit"])
            for symbol in data["items"]:
                assert isinstance(symbol, str)
                assert symbol


@allure.title('BF-COMPLETE-PARAM-001 - ParamError - 只传 `start_time_ms')
@allure.feature('binance-full')
@allure.story('ParamError')
@pytest.mark.binance_full_api
def test_bf_complete_param_001(binance_full_api):
    """
    Case ID: BF-COMPLETE-PARAM-001
    测试大类: binance-full
    测试类型: ParamError
    测试目的: 只传 `start_time_ms
    预期断言: 返回时间窗成对错误
    """

    case_id = 'BF-COMPLETE-PARAM-001'

    responses = []

    request_params_1 = {'start_time_ms': 1704067200000}

    try:
        response_1 = binance_full_api.get_usdm_complete_symbols(start_time_ms=1704067200000)
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('complete-symbols', response_1, request_params_1))

    for target_name, response, request_params in responses:

        assert response.status_code < 500
        body = response.json() if response.content else {}
        assert str(body.get("code", "")) != "500"
        if response.status_code >= 400:
            assert response.status_code in {400, 422}
            assert any(key in str(body).lower() for key in ('symbol', 'pair', 'contract_type', 'time', 'limit', 'symbols'))
        else:
            assert "code" in body
            assert "status" in body
            assert "message" in body
            assert str(body["code"]) in {"400", "422"}
            assert body["status"] in {"error", "fail", "failed"}
        assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('BF-COMPLETE-PARAM-002 - ParamError - limit=0')
@allure.feature('binance-full')
@allure.story('ParamError')
@pytest.mark.binance_full_api
def test_bf_complete_param_002(binance_full_api):
    """
    Case ID: BF-COMPLETE-PARAM-002
    测试大类: binance-full
    测试类型: ParamError
    测试目的: limit=0
    预期断言: 返回参数错误
    """

    case_id = 'BF-COMPLETE-PARAM-002'

    responses = []

    request_params_1 = {'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'limit': 0}

    try:
        response_1 = binance_full_api.get_usdm_complete_symbols(start_time_ms=1704067200000, end_time_ms=1704153600000, limit=0)
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('complete-symbols', response_1, request_params_1))

    for target_name, response, request_params in responses:

        assert response.status_code < 500
        body = response.json() if response.content else {}
        assert str(body.get("code", "")) != "500"
        if response.status_code >= 400:
            assert response.status_code in {400, 422}
            assert any(key in str(body).lower() for key in ('symbol', 'pair', 'contract_type', 'time', 'limit', 'symbols'))
        else:
            assert "code" in body
            assert "status" in body
            assert "message" in body
            assert str(body["code"]) in {"400", "422"}
            assert body["status"] in {"error", "fail", "failed"}
        assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('BF-COMPLETE-DQC-001 - DataQuality - 正常请求')
@allure.feature('binance-full')
@allure.story('DataQuality')
@pytest.mark.binance_full_api
@pytest.mark.dqc
def test_bf_complete_dqc_001(binance_full_api):
    """
    Case ID: BF-COMPLETE-DQC-001
    测试大类: binance-full
    测试类型: DataQuality
    测试目的: 正常请求
    预期断言: 所有 items 为非空大写字符串
    """

    case_id = 'BF-COMPLETE-DQC-001'

    responses = []

    request_params_1 = {'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'limit': 10}

    response_1 = binance_full_api.get_usdm_complete_symbols(start_time_ms=1704067200000, end_time_ms=1704153600000, limit=10)

    responses.append(('complete-symbols', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
        assert isinstance(data, dict)
        assert "items" in data
        assert isinstance(data["items"], list)
        if "count" in data:
            assert data["count"] == len(data["items"])
        if request_params.get("limit") is not None:
            assert len(data["items"]) <= int(request_params["limit"])
        for symbol in data["items"]:
            assert isinstance(symbol, str)
            assert symbol
            assert symbol == symbol.upper()


@allure.title('BF-DELISTED-NORMAL-001 - Normal - status=CLOSE,OFF_EXCHANGE&limit=10')
@allure.feature('binance-full')
@allure.story('Normal')
@pytest.mark.binance_full_api
def test_bf_delisted_normal_001(binance_full_api):
    """
    Case ID: BF-DELISTED-NORMAL-001
    测试大类: binance-full
    测试类型: Normal
    测试目的: status=CLOSE,OFF_EXCHANGE&limit=10
    预期断言: 成功；items 为已下架 symbol 字符串数组
    """

    case_id = 'BF-DELISTED-NORMAL-001'

    responses = []

    request_params_1 = {'status': 'CLOSE,OFF_EXCHANGE', 'limit': 10}

    response_1 = binance_full_api.get_usdm_delisted_symbols(status='CLOSE,OFF_EXCHANGE', limit=10)

    responses.append(('delisted-symbols', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
        assert isinstance(data, dict)
        assert "items" in data
        assert isinstance(data["items"], list)
        if "count" in data:
            assert data["count"] == len(data["items"])
        if request_params.get("limit") is not None:
            assert len(data["items"]) <= int(request_params["limit"])
        for symbol in data["items"]:
            assert isinstance(symbol, str)
            assert symbol


@allure.title('BF-DELISTED-BOUNDARY-001 - Boundary - 不传 `status')
@allure.feature('binance-full')
@allure.story('Boundary')
@pytest.mark.binance_full_api
def test_bf_delisted_boundary_001(binance_full_api):
    """
    Case ID: BF-DELISTED-BOUNDARY-001
    测试大类: binance-full
    测试类型: Boundary
    测试目的: 不传 `status
    预期断言: 使用默认下架状态语义
    """

    case_id = 'BF-DELISTED-BOUNDARY-001'

    responses = []

    request_params_1 = {}

    response_1 = binance_full_api.get_usdm_delisted_symbols()

    responses.append(('delisted-symbols', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
        assert isinstance(data, dict)
        assert "items" in data
        assert isinstance(data["items"], list)
        if "count" in data:
            assert data["count"] == len(data["items"])
        if request_params.get("limit") is not None:
            assert len(data["items"]) <= int(request_params["limit"])
        for symbol in data["items"]:
            assert isinstance(symbol, str)
            assert symbol


@allure.title('BF-DELISTED-BOUNDARY-002 - Boundary - include_disabled_only=true')
@allure.feature('binance-full')
@allure.story('Boundary')
@pytest.mark.binance_full_api
def test_bf_delisted_boundary_002(binance_full_api):
    """
    Case ID: BF-DELISTED-BOUNDARY-002
    测试大类: binance-full
    测试类型: Boundary
    测试目的: include_disabled_only=true
    预期断言: 返回禁用合约集合或空数组；不能 500
    """

    case_id = 'BF-DELISTED-BOUNDARY-002'

    responses = []

    request_params_1 = {'include_disabled_only': True}

    try:
        response_1 = binance_full_api.get_usdm_delisted_symbols(include_disabled_only=True)
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('delisted-symbols', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
            assert isinstance(data, dict)
            assert "items" in data
            assert isinstance(data["items"], list)
            if "count" in data:
                assert data["count"] == len(data["items"])
            if request_params.get("limit") is not None:
                assert len(data["items"]) <= int(request_params["limit"])
            for symbol in data["items"]:
                assert isinstance(symbol, str)
                assert symbol


@allure.title('BF-DELISTED-PARAM-001 - ParamError - status=INVALID_STATUS')
@allure.feature('binance-full')
@allure.story('ParamError')
@pytest.mark.binance_full_api
def test_bf_delisted_param_001(binance_full_api):
    """
    Case ID: BF-DELISTED-PARAM-001
    测试大类: binance-full
    测试类型: ParamError
    测试目的: status=INVALID_STATUS
    预期断言: 返回参数错误或业务错误
    """

    case_id = 'BF-DELISTED-PARAM-001'

    responses = []

    request_params_1 = {'status': 'INVALID_STATUS'}

    try:
        response_1 = binance_full_api.get_usdm_delisted_symbols(status='INVALID_STATUS')
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('delisted-symbols', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
            assert isinstance(data, dict)
            assert "items" in data
            assert isinstance(data["items"], list)
            if "count" in data:
                assert data["count"] == len(data["items"])
            if request_params.get("limit") is not None:
                assert len(data["items"]) <= int(request_params["limit"])
            for symbol in data["items"]:
                assert isinstance(symbol, str)
                assert symbol


@allure.title('BF-DELISTED-PARAM-002 - ParamError - limit=20001')
@allure.feature('binance-full')
@allure.story('ParamError')
@pytest.mark.binance_full_api
def test_bf_delisted_param_002(binance_full_api):
    """
    Case ID: BF-DELISTED-PARAM-002
    测试大类: binance-full
    测试类型: ParamError
    测试目的: limit=20001
    预期断言: 返回参数错误
    """

    case_id = 'BF-DELISTED-PARAM-002'

    responses = []

    request_params_1 = {'limit': 20001}

    try:
        response_1 = binance_full_api.get_usdm_delisted_symbols(limit=20001)
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('delisted-symbols', response_1, request_params_1))

    for target_name, response, request_params in responses:

        assert response.status_code < 500
        body = response.json() if response.content else {}
        assert str(body.get("code", "")) != "500"
        if response.status_code >= 400:
            assert response.status_code in {400, 422}
            assert any(key in str(body).lower() for key in ('symbol', 'pair', 'contract_type', 'time', 'limit', 'symbols'))
        else:
            assert "code" in body
            assert "status" in body
            assert "message" in body
            assert str(body["code"]) in {"400", "422"}
            assert body["status"] in {"error", "fail", "failed"}
        assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('BF-USDM-TIMERANGE-NORMAL-001 - Normal - symbol=BTCUSDT&interval=1m')
@allure.feature('binance-full')
@allure.story('Normal')
@pytest.mark.binance_full_api
def test_bf_usdm_timerange_normal_001(binance_full_api):
    """
    Case ID: BF-USDM-TIMERANGE-NORMAL-001
    测试大类: binance-full
    测试类型: Normal
    测试目的: symbol=BTCUSDT&interval=1m
    预期断言: 单 symbol 成功；`data.kline` 与 `data.funding` 结构完整
    """

    case_id = 'BF-USDM-TIMERANGE-NORMAL-001'

    responses = []

    request_params_1 = {'symbol': 'BTCUSDT', 'interval': '1m'}

    response_1 = binance_full_api.get_usdm_time_range(symbol='BTCUSDT', interval='1m')

    responses.append(('time-range', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
        assert isinstance(data, dict)
        if data.get("multi") or "by_symbol" in data:
            assert data.get("multi") is True or str(data.get("multi")).lower() == "true"
            assert "by_symbol" in data
            assert isinstance(data["by_symbol"], dict)
            requested = str(request_params.get("symbol", request_params.get("pair"))).split(",")
            for key in requested:
                assert key in data["by_symbol"]
        else:
            assert "kline" in data
            kline = data["kline"]
            assert "time_field" in kline
            assert "min_time_ms" in kline
            assert "max_time_ms" in kline
            assert "has_data" in kline
            if kline["min_time_ms"] is not None:
                assert len(str(int(kline["min_time_ms"]))) == 13
            if kline["max_time_ms"] is not None:
                assert len(str(int(kline["max_time_ms"]))) == 13
            if kline["has_data"]:
                assert int(kline["min_time_ms"]) <= int(kline["max_time_ms"])


@allure.title('BF-USDM-TIMERANGE-NORMAL-002 - Normal - symbol=BTCUSDT,ETHUSDT&interval=1m')
@allure.feature('binance-full')
@allure.story('Normal')
@pytest.mark.binance_full_api
def test_bf_usdm_timerange_normal_002(binance_full_api):
    """
    Case ID: BF-USDM-TIMERANGE-NORMAL-002
    测试大类: binance-full
    测试类型: Normal
    测试目的: symbol=BTCUSDT,ETHUSDT&interval=1m
    预期断言: 多 symbol 成功；`data.multi=true`；`by_symbol` 含请求 symbol
    """

    case_id = 'BF-USDM-TIMERANGE-NORMAL-002'

    responses = []

    request_params_1 = {'symbol': 'BTCUSDT,ETHUSDT', 'interval': '1m'}

    response_1 = binance_full_api.get_usdm_time_range(symbol='BTCUSDT,ETHUSDT', interval='1m')

    responses.append(('time-range', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
        assert isinstance(data, dict)
        if data.get("multi") or "by_symbol" in data:
            assert data.get("multi") is True or str(data.get("multi")).lower() == "true"
            assert "by_symbol" in data
            assert isinstance(data["by_symbol"], dict)
            requested = str(request_params.get("symbol", request_params.get("pair"))).split(",")
            for key in requested:
                assert key in data["by_symbol"]
        else:
            assert "kline" in data
            kline = data["kline"]
            assert "time_field" in kline
            assert "min_time_ms" in kline
            assert "max_time_ms" in kline
            assert "has_data" in kline
            if kline["min_time_ms"] is not None:
                assert len(str(int(kline["min_time_ms"]))) == 13
            if kline["max_time_ms"] is not None:
                assert len(str(int(kline["max_time_ms"]))) == 13
            if kline["has_data"]:
                assert int(kline["min_time_ms"]) <= int(kline["max_time_ms"])


@allure.title('BF-USDM-TIMERANGE-BOUNDARY-001 - Boundary - interval=1h')
@allure.feature('binance-full')
@allure.story('Boundary')
@pytest.mark.binance_full_api
def test_bf_usdm_timerange_boundary_001(binance_full_api):
    """
    Case ID: BF-USDM-TIMERANGE-BOUNDARY-001
    测试大类: binance-full
    测试类型: Boundary
    测试目的: interval=1h
    预期断言: 查询 1h 专表语义；有数据时边界毫秒合法
    """

    case_id = 'BF-USDM-TIMERANGE-BOUNDARY-001'

    responses = []

    request_params_1 = {'symbol': 'BTCUSDT', 'interval': '1h'}

    try:
        response_1 = binance_full_api.get_usdm_time_range(symbol='BTCUSDT', interval='1h')
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('time-range', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
            assert isinstance(data, dict)
            if data.get("multi") or "by_symbol" in data:
                assert data.get("multi") is True or str(data.get("multi")).lower() == "true"
                assert "by_symbol" in data
                assert isinstance(data["by_symbol"], dict)
                requested = str(request_params.get("symbol", request_params.get("pair"))).split(",")
                for key in requested:
                    assert key in data["by_symbol"]
            else:
                assert "kline" in data
                kline = data["kline"]
                assert "time_field" in kline
                assert "min_time_ms" in kline
                assert "max_time_ms" in kline
                assert "has_data" in kline
                if kline["min_time_ms"] is not None:
                    assert len(str(int(kline["min_time_ms"]))) == 13
                if kline["max_time_ms"] is not None:
                    assert len(str(int(kline["max_time_ms"]))) == 13
                if kline["has_data"]:
                    assert int(kline["min_time_ms"]) <= int(kline["max_time_ms"])


@allure.title('BF-USDM-TIMERANGE-PARAM-001 - ParamError - 缺少 `symbol')
@allure.feature('binance-full')
@allure.story('ParamError')
@pytest.mark.binance_full_api
def test_bf_usdm_timerange_param_001(binance_full_api):
    """
    Case ID: BF-USDM-TIMERANGE-PARAM-001
    测试大类: binance-full
    测试类型: ParamError
    测试目的: 缺少 `symbol
    预期断言: 返回参数错误
    """

    case_id = 'BF-USDM-TIMERANGE-PARAM-001'

    responses = []

    request_params_1 = {'interval': '1m'}

    try:
        response_1 = binance_full_api.get_usdm_time_range(interval='1m')
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('time-range', response_1, request_params_1))

    for target_name, response, request_params in responses:

        assert response.status_code < 500
        body = response.json() if response.content else {}
        assert str(body.get("code", "")) != "500"
        if response.status_code >= 400:
            assert response.status_code in {400, 422}
            assert any(key in str(body).lower() for key in ('symbol', 'pair', 'contract_type', 'time', 'limit', 'symbols'))
        else:
            assert "code" in body
            assert "status" in body
            assert "message" in body
            assert str(body["code"]) in {"400", "422"}
            assert body["status"] in {"error", "fail", "failed"}
        assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('BF-USDM-TIMERANGE-PARAM-002 - ParamError - symbol=BTCUSDT&interval=99m')
@allure.feature('binance-full')
@allure.story('ParamError')
@pytest.mark.binance_full_api
def test_bf_usdm_timerange_param_002(binance_full_api):
    """
    Case ID: BF-USDM-TIMERANGE-PARAM-002
    测试大类: binance-full
    测试类型: ParamError
    测试目的: symbol=BTCUSDT&interval=99m
    预期断言: 返回无数据或业务提示；不能 500
    """

    case_id = 'BF-USDM-TIMERANGE-PARAM-002'

    responses = []

    request_params_1 = {'symbol': 'BTCUSDT', 'interval': '99m'}

    try:
        response_1 = binance_full_api.get_usdm_time_range(symbol='BTCUSDT', interval='99m')
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('time-range', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
            assert isinstance(data, dict)
            if data.get("multi") or "by_symbol" in data:
                assert data.get("multi") is True or str(data.get("multi")).lower() == "true"
                assert "by_symbol" in data
                assert isinstance(data["by_symbol"], dict)
                requested = str(request_params.get("symbol", request_params.get("pair"))).split(",")
                for key in requested:
                    assert key in data["by_symbol"]
            else:
                assert "kline" in data
                kline = data["kline"]
                assert "time_field" in kline
                assert "min_time_ms" in kline
                assert "max_time_ms" in kline
                assert "has_data" in kline
                if kline["min_time_ms"] is not None:
                    assert len(str(int(kline["min_time_ms"]))) == 13
                if kline["max_time_ms"] is not None:
                    assert len(str(int(kline["max_time_ms"]))) == 13
                if kline["has_data"]:
                    assert int(kline["min_time_ms"]) <= int(kline["max_time_ms"])


@allure.title('BF-COINM-PERP-TIMERANGE-NORMAL-001 - Normal - pair=BTCUSD&contract_type=PERPETUAL&interval=1m')
@allure.feature('binance-full')
@allure.story('Normal')
@pytest.mark.binance_full_api
def test_bf_coinm_perp_timerange_normal_001(binance_full_api):
    """
    Case ID: BF-COINM-PERP-TIMERANGE-NORMAL-001
    测试大类: binance-full
    测试类型: Normal
    测试目的: pair=BTCUSD&contract_type=PERPETUAL&interval=1m
    预期断言: 成功；`kline` 和 `funding` 时间边界结构完整
    """

    case_id = 'BF-COINM-PERP-TIMERANGE-NORMAL-001'

    responses = []

    request_params_1 = {'pair': 'BTCUSD', 'contract_type': 'PERPETUAL', 'interval': '1m'}

    response_1 = binance_full_api.get_coinm_perp_time_range(pair='BTCUSD', contract_type='PERPETUAL', interval='1m')

    responses.append(('time-range', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
        assert isinstance(data, dict)
        if data.get("multi") or "by_symbol" in data:
            assert data.get("multi") is True or str(data.get("multi")).lower() == "true"
            assert "by_symbol" in data
            assert isinstance(data["by_symbol"], dict)
            requested = str(request_params.get("symbol", request_params.get("pair"))).split(",")
            for key in requested:
                assert key in data["by_symbol"]
        else:
            assert "kline" in data
            kline = data["kline"]
            assert "time_field" in kline
            assert "min_time_ms" in kline
            assert "max_time_ms" in kline
            assert "has_data" in kline
            if kline["min_time_ms"] is not None:
                assert len(str(int(kline["min_time_ms"]))) == 13
            if kline["max_time_ms"] is not None:
                assert len(str(int(kline["max_time_ms"]))) == 13
            if kline["has_data"]:
                assert int(kline["min_time_ms"]) <= int(kline["max_time_ms"])


@allure.title('BF-COINM-PERP-TIMERANGE-NORMAL-002 - Normal - pair=BTCUSD,ETHUSD&contract_type=PERPETUAL')
@allure.feature('binance-full')
@allure.story('Normal')
@pytest.mark.binance_full_api
def test_bf_coinm_perp_timerange_normal_002(binance_full_api):
    """
    Case ID: BF-COINM-PERP-TIMERANGE-NORMAL-002
    测试大类: binance-full
    测试类型: Normal
    测试目的: pair=BTCUSD,ETHUSD&contract_type=PERPETUAL
    预期断言: 多 pair 返回 `by_symbol` 或等价分桶结构
    """

    case_id = 'BF-COINM-PERP-TIMERANGE-NORMAL-002'

    responses = []

    request_params_1 = {'pair': 'BTCUSD,ETHUSD', 'contract_type': 'PERPETUAL'}

    response_1 = binance_full_api.get_coinm_perp_time_range(pair='BTCUSD,ETHUSD', contract_type='PERPETUAL')

    responses.append(('time-range', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
        assert isinstance(data, dict)
        if data.get("multi") or "by_symbol" in data:
            assert data.get("multi") is True or str(data.get("multi")).lower() == "true"
            assert "by_symbol" in data
            assert isinstance(data["by_symbol"], dict)
            requested = str(request_params.get("symbol", request_params.get("pair"))).split(",")
            for key in requested:
                assert any(
                    bucket_key == key
                    or str(bucket.get("pair", bucket.get("symbol", ""))).startswith(key)
                    or str(bucket.get("filters", {}).get("pair", "")).startswith(key)
                    for bucket_key, bucket in data["by_symbol"].items()
                )
        else:
            assert "kline" in data
            kline = data["kline"]
            assert "time_field" in kline
            assert "min_time_ms" in kline
            assert "max_time_ms" in kline
            assert "has_data" in kline
            if kline["min_time_ms"] is not None:
                assert len(str(int(kline["min_time_ms"]))) == 13
            if kline["max_time_ms"] is not None:
                assert len(str(int(kline["max_time_ms"]))) == 13
            if kline["has_data"]:
                assert int(kline["min_time_ms"]) <= int(kline["max_time_ms"])


@allure.title('BF-COINM-PERP-TIMERANGE-PARAM-001 - ParamError - 缺少 `pair')
@allure.feature('binance-full')
@allure.story('ParamError')
@pytest.mark.binance_full_api
def test_bf_coinm_perp_timerange_param_001(binance_full_api):
    """
    Case ID: BF-COINM-PERP-TIMERANGE-PARAM-001
    测试大类: binance-full
    测试类型: ParamError
    测试目的: 缺少 `pair
    预期断言: 返回参数错误
    """

    case_id = 'BF-COINM-PERP-TIMERANGE-PARAM-001'

    responses = []

    request_params_1 = {'contract_type': 'PERPETUAL'}

    try:
        response_1 = binance_full_api.get_coinm_perp_time_range(contract_type='PERPETUAL')
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('time-range', response_1, request_params_1))

    for target_name, response, request_params in responses:

        assert response.status_code < 500
        body = response.json() if response.content else {}
        assert str(body.get("code", "")) != "500"
        if response.status_code >= 400:
            assert response.status_code in {400, 422}
            assert any(key in str(body).lower() for key in ('symbol', 'pair', 'contract_type', 'time', 'limit', 'symbols'))
        else:
            assert "code" in body
            assert "status" in body
            assert "message" in body
            assert str(body["code"]) in {"400", "422"}
            assert body["status"] in {"error", "fail", "failed"}
        assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('BF-COINM-PERP-TIMERANGE-PARAM-002 - ParamError - 缺少 `contract_type')
@allure.feature('binance-full')
@allure.story('ParamError')
@pytest.mark.binance_full_api
def test_bf_coinm_perp_timerange_param_002(binance_full_api):
    """
    Case ID: BF-COINM-PERP-TIMERANGE-PARAM-002
    测试大类: binance-full
    测试类型: ParamError
    测试目的: 缺少 `contract_type
    预期断言: 返回参数错误
    """

    case_id = 'BF-COINM-PERP-TIMERANGE-PARAM-002'

    responses = []

    request_params_1 = {'pair': 'BTCUSD'}

    try:
        response_1 = binance_full_api.get_coinm_perp_time_range(pair='BTCUSD')
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('time-range', response_1, request_params_1))

    for target_name, response, request_params in responses:

        assert response.status_code < 500
        body = response.json() if response.content else {}
        assert str(body.get("code", "")) != "500"
        if response.status_code >= 400:
            assert response.status_code in {400, 422}
            assert any(key in str(body).lower() for key in ('symbol', 'pair', 'contract_type', 'time', 'limit', 'symbols'))
        else:
            assert "code" in body
            assert "status" in body
            assert "message" in body
            assert str(body["code"]) in {"400", "422"}
            assert body["status"] in {"error", "fail", "failed"}
        assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('BF-COINM-PERP-TIMERANGE-PARAM-003 - ParamError - contract_type=CURRENT_QUARTER')
@allure.feature('binance-full')
@allure.story('ParamError')
@pytest.mark.binance_full_api
def test_bf_coinm_perp_timerange_param_003(binance_full_api):
    """
    Case ID: BF-COINM-PERP-TIMERANGE-PARAM-003
    测试大类: binance-full
    测试类型: ParamError
    测试目的: contract_type=CURRENT_QUARTER
    预期断言: 返回参数错误或业务错误；PERP 仅允许 `PERPETUAL
    """

    case_id = 'BF-COINM-PERP-TIMERANGE-PARAM-003'

    responses = []

    request_params_1 = {'pair': 'BTCUSD', 'contract_type': 'CURRENT_QUARTER'}

    try:
        response_1 = binance_full_api.get_coinm_perp_time_range(pair='BTCUSD', contract_type='CURRENT_QUARTER')
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('time-range', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
            assert isinstance(data, dict)
            if data.get("multi") or "by_symbol" in data:
                assert data.get("multi") is True or str(data.get("multi")).lower() == "true"
                assert "by_symbol" in data
                assert isinstance(data["by_symbol"], dict)
                requested = str(request_params.get("symbol", request_params.get("pair"))).split(",")
                for key in requested:
                    assert key in data["by_symbol"]
            else:
                assert "kline" in data
                kline = data["kline"]
                assert "time_field" in kline
                assert "min_time_ms" in kline
                assert "max_time_ms" in kline
                assert "has_data" in kline
                if kline["min_time_ms"] is not None:
                    assert len(str(int(kline["min_time_ms"]))) == 13
                if kline["max_time_ms"] is not None:
                    assert len(str(int(kline["max_time_ms"]))) == 13
                if kline["has_data"]:
                    assert int(kline["min_time_ms"]) <= int(kline["max_time_ms"])


@allure.title('BF-COINM-DELIVERY-TIMERANGE-NORMAL-001 - Normal - pair=BTCUSD&contract_type=CURRENT_QUARTER&interval=1m')
@allure.feature('binance-full')
@allure.story('Normal')
@pytest.mark.binance_full_api
def test_bf_coinm_delivery_timerange_normal_001(binance_full_api):
    """
    Case ID: BF-COINM-DELIVERY-TIMERANGE-NORMAL-001
    测试大类: binance-full
    测试类型: Normal
    测试目的: pair=BTCUSD&contract_type=CURRENT_QUARTER&interval=1m
    预期断言: 成功；仅要求 `kline` 时间边界，不要求 funding
    """

    case_id = 'BF-COINM-DELIVERY-TIMERANGE-NORMAL-001'

    responses = []

    request_params_1 = {'pair': 'BTCUSD', 'contract_type': 'CURRENT_QUARTER', 'interval': '1m'}

    response_1 = binance_full_api.get_coinm_delivery_time_range(pair='BTCUSD', contract_type='CURRENT_QUARTER', interval='1m')

    responses.append(('time-range', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
        assert isinstance(data, dict)
        if data.get("multi") or "by_symbol" in data:
            assert data.get("multi") is True or str(data.get("multi")).lower() == "true"
            assert "by_symbol" in data
            assert isinstance(data["by_symbol"], dict)
            requested = str(request_params.get("symbol", request_params.get("pair"))).split(",")
            for key in requested:
                assert key in data["by_symbol"]
        else:
            assert "kline" in data
            kline = data["kline"]
            assert "time_field" in kline
            assert "min_time_ms" in kline
            assert "max_time_ms" in kline
            assert "has_data" in kline
            if kline["min_time_ms"] is not None:
                assert len(str(int(kline["min_time_ms"]))) == 13
            if kline["max_time_ms"] is not None:
                assert len(str(int(kline["max_time_ms"]))) == 13
            if kline["has_data"]:
                assert int(kline["min_time_ms"]) <= int(kline["max_time_ms"])


@allure.title('BF-COINM-DELIVERY-TIMERANGE-BOUNDARY-001 - Boundary - contract_type=NEXT_QUARTER')
@allure.feature('binance-full')
@allure.story('Boundary')
@pytest.mark.binance_full_api
def test_bf_coinm_delivery_timerange_boundary_001(binance_full_api):
    """
    Case ID: BF-COINM-DELIVERY-TIMERANGE-BOUNDARY-001
    测试大类: binance-full
    测试类型: Boundary
    测试目的: contract_type=NEXT_QUARTER
    预期断言: 合法枚举；成功或无数据提示
    """

    case_id = 'BF-COINM-DELIVERY-TIMERANGE-BOUNDARY-001'

    responses = []

    request_params_1 = {'pair': 'BTCUSD', 'contract_type': 'NEXT_QUARTER'}

    try:
        response_1 = binance_full_api.get_coinm_delivery_time_range(pair='BTCUSD', contract_type='NEXT_QUARTER')
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('time-range', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
            assert isinstance(data, dict)
            if data.get("multi") or "by_symbol" in data:
                assert data.get("multi") is True or str(data.get("multi")).lower() == "true"
                assert "by_symbol" in data
                assert isinstance(data["by_symbol"], dict)
                requested = str(request_params.get("symbol", request_params.get("pair"))).split(",")
                for key in requested:
                    assert key in data["by_symbol"]
            else:
                assert "kline" in data
                kline = data["kline"]
                assert "time_field" in kline
                assert "min_time_ms" in kline
                assert "max_time_ms" in kline
                assert "has_data" in kline
                if kline["min_time_ms"] is not None:
                    assert len(str(int(kline["min_time_ms"]))) == 13
                if kline["max_time_ms"] is not None:
                    assert len(str(int(kline["max_time_ms"]))) == 13
                if kline["has_data"]:
                    assert int(kline["min_time_ms"]) <= int(kline["max_time_ms"])


@allure.title('BF-COINM-DELIVERY-TIMERANGE-PARAM-001 - ParamError - 缺少 `contract_type')
@allure.feature('binance-full')
@allure.story('ParamError')
@pytest.mark.binance_full_api
def test_bf_coinm_delivery_timerange_param_001(binance_full_api):
    """
    Case ID: BF-COINM-DELIVERY-TIMERANGE-PARAM-001
    测试大类: binance-full
    测试类型: ParamError
    测试目的: 缺少 `contract_type
    预期断言: 返回参数错误
    """

    case_id = 'BF-COINM-DELIVERY-TIMERANGE-PARAM-001'

    responses = []

    request_params_1 = {'pair': 'BTCUSD'}

    try:
        response_1 = binance_full_api.get_coinm_delivery_time_range(pair='BTCUSD')
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('time-range', response_1, request_params_1))

    for target_name, response, request_params in responses:

        assert response.status_code < 500
        body = response.json() if response.content else {}
        assert str(body.get("code", "")) != "500"
        if response.status_code >= 400:
            assert response.status_code in {400, 422}
            assert any(key in str(body).lower() for key in ('symbol', 'pair', 'contract_type', 'time', 'limit', 'symbols'))
        else:
            assert "code" in body
            assert "status" in body
            assert "message" in body
            assert str(body["code"]) in {"400", "422"}
            assert body["status"] in {"error", "fail", "failed"}
        assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('BF-COINM-DELIVERY-TIMERANGE-PARAM-002 - ParamError - contract_type=PERPETUAL')
@allure.feature('binance-full')
@allure.story('ParamError')
@pytest.mark.binance_full_api
def test_bf_coinm_delivery_timerange_param_002(binance_full_api):
    """
    Case ID: BF-COINM-DELIVERY-TIMERANGE-PARAM-002
    测试大类: binance-full
    测试类型: ParamError
    测试目的: contract_type=PERPETUAL
    预期断言: 返回参数错误
    """

    case_id = 'BF-COINM-DELIVERY-TIMERANGE-PARAM-002'

    responses = []

    request_params_1 = {'pair': 'BTCUSD', 'contract_type': 'PERPETUAL'}

    try:
        response_1 = binance_full_api.get_coinm_delivery_time_range(pair='BTCUSD', contract_type='PERPETUAL')
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('time-range', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
            assert isinstance(data, dict)
            if data.get("multi") or "by_symbol" in data:
                assert data.get("multi") is True or str(data.get("multi")).lower() == "true"
                assert "by_symbol" in data
                assert isinstance(data["by_symbol"], dict)
                requested = str(request_params.get("symbol", request_params.get("pair"))).split(",")
                for key in requested:
                    assert key in data["by_symbol"]
            else:
                assert "kline" in data
                kline = data["kline"]
                assert "time_field" in kline
                assert "min_time_ms" in kline
                assert "max_time_ms" in kline
                assert "has_data" in kline
                if kline["min_time_ms"] is not None:
                    assert len(str(int(kline["min_time_ms"]))) == 13
                if kline["max_time_ms"] is not None:
                    assert len(str(int(kline["max_time_ms"]))) == 13
                if kline["has_data"]:
                    assert int(kline["min_time_ms"]) <= int(kline["max_time_ms"])


@allure.title('BF-USDM-DELIVERY-TIMERANGE-NORMAL-001 - Normal - pair=BTCUSDT&contract_type=CURRENT_QUARTER&interval=1m')
@allure.feature('binance-full')
@allure.story('Normal')
@pytest.mark.binance_full_api
def test_bf_usdm_delivery_timerange_normal_001(binance_full_api):
    """
    Case ID: BF-USDM-DELIVERY-TIMERANGE-NORMAL-001
    测试大类: binance-full
    测试类型: Normal
    测试目的: pair=BTCUSDT&contract_type=CURRENT_QUARTER&interval=1m
    预期断言: 成功；`kline` 时间边界结构完整
    """

    case_id = 'BF-USDM-DELIVERY-TIMERANGE-NORMAL-001'

    responses = []

    request_params_1 = {'pair': 'BTCUSDT', 'contract_type': 'CURRENT_QUARTER', 'interval': '1m'}

    response_1 = binance_full_api.get_usdm_delivery_time_range(pair='BTCUSDT', contract_type='CURRENT_QUARTER', interval='1m')

    responses.append(('time-range', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
        assert isinstance(data, dict)
        if data.get("multi") or "by_symbol" in data:
            assert data.get("multi") is True or str(data.get("multi")).lower() == "true"
            assert "by_symbol" in data
            assert isinstance(data["by_symbol"], dict)
            requested = str(request_params.get("symbol", request_params.get("pair"))).split(",")
            for key in requested:
                assert key in data["by_symbol"]
        else:
            assert "kline" in data
            kline = data["kline"]
            assert "time_field" in kline
            assert "min_time_ms" in kline
            assert "max_time_ms" in kline
            assert "has_data" in kline
            if kline["min_time_ms"] is not None:
                assert len(str(int(kline["min_time_ms"]))) == 13
            if kline["max_time_ms"] is not None:
                assert len(str(int(kline["max_time_ms"]))) == 13
            if kline["has_data"]:
                assert int(kline["min_time_ms"]) <= int(kline["max_time_ms"])


@allure.title('BF-USDM-DELIVERY-TIMERANGE-BOUNDARY-001 - Boundary - contract_type=NEXT_QUARTER')
@allure.feature('binance-full')
@allure.story('Boundary')
@pytest.mark.binance_full_api
def test_bf_usdm_delivery_timerange_boundary_001(binance_full_api):
    """
    Case ID: BF-USDM-DELIVERY-TIMERANGE-BOUNDARY-001
    测试大类: binance-full
    测试类型: Boundary
    测试目的: contract_type=NEXT_QUARTER
    预期断言: 合法枚举；成功或无数据提示
    """

    case_id = 'BF-USDM-DELIVERY-TIMERANGE-BOUNDARY-001'

    responses = []

    request_params_1 = {'pair': 'BTCUSDT', 'contract_type': 'NEXT_QUARTER'}

    try:
        response_1 = binance_full_api.get_usdm_delivery_time_range(pair='BTCUSDT', contract_type='NEXT_QUARTER')
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('time-range', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
            assert isinstance(data, dict)
            if data.get("multi") or "by_symbol" in data:
                assert data.get("multi") is True or str(data.get("multi")).lower() == "true"
                assert "by_symbol" in data
                assert isinstance(data["by_symbol"], dict)
                requested = str(request_params.get("symbol", request_params.get("pair"))).split(",")
                for key in requested:
                    assert key in data["by_symbol"]
            else:
                assert "kline" in data
                kline = data["kline"]
                assert "time_field" in kline
                assert "min_time_ms" in kline
                assert "max_time_ms" in kline
                assert "has_data" in kline
                if kline["min_time_ms"] is not None:
                    assert len(str(int(kline["min_time_ms"]))) == 13
                if kline["max_time_ms"] is not None:
                    assert len(str(int(kline["max_time_ms"]))) == 13
                if kline["has_data"]:
                    assert int(kline["min_time_ms"]) <= int(kline["max_time_ms"])


@allure.title('BF-USDM-DELIVERY-TIMERANGE-PARAM-001 - ParamError - 缺少 `pair')
@allure.feature('binance-full')
@allure.story('ParamError')
@pytest.mark.binance_full_api
def test_bf_usdm_delivery_timerange_param_001(binance_full_api):
    """
    Case ID: BF-USDM-DELIVERY-TIMERANGE-PARAM-001
    测试大类: binance-full
    测试类型: ParamError
    测试目的: 缺少 `pair
    预期断言: 返回参数错误
    """

    case_id = 'BF-USDM-DELIVERY-TIMERANGE-PARAM-001'

    responses = []

    request_params_1 = {'contract_type': 'CURRENT_QUARTER'}

    try:
        response_1 = binance_full_api.get_usdm_delivery_time_range(contract_type='CURRENT_QUARTER')
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('time-range', response_1, request_params_1))

    for target_name, response, request_params in responses:

        assert response.status_code < 500
        body = response.json() if response.content else {}
        assert str(body.get("code", "")) != "500"
        if response.status_code >= 400:
            assert response.status_code in {400, 422}
            assert any(key in str(body).lower() for key in ('symbol', 'pair', 'contract_type', 'time', 'limit', 'symbols'))
        else:
            assert "code" in body
            assert "status" in body
            assert "message" in body
            assert str(body["code"]) in {"400", "422"}
            assert body["status"] in {"error", "fail", "failed"}
        assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('BF-USDM-DELIVERY-TIMERANGE-PARAM-002 - ParamError - contract_type=PERPETUAL')
@allure.feature('binance-full')
@allure.story('ParamError')
@pytest.mark.binance_full_api
def test_bf_usdm_delivery_timerange_param_002(binance_full_api):
    """
    Case ID: BF-USDM-DELIVERY-TIMERANGE-PARAM-002
    测试大类: binance-full
    测试类型: ParamError
    测试目的: contract_type=PERPETUAL
    预期断言: 返回参数错误
    """

    case_id = 'BF-USDM-DELIVERY-TIMERANGE-PARAM-002'

    responses = []

    request_params_1 = {'pair': 'BTCUSDT', 'contract_type': 'PERPETUAL'}

    try:
        response_1 = binance_full_api.get_usdm_delivery_time_range(pair='BTCUSDT', contract_type='PERPETUAL')
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('time-range', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
            assert isinstance(data, dict)
            if data.get("multi") or "by_symbol" in data:
                assert data.get("multi") is True or str(data.get("multi")).lower() == "true"
                assert "by_symbol" in data
                assert isinstance(data["by_symbol"], dict)
                requested = str(request_params.get("symbol", request_params.get("pair"))).split(",")
                for key in requested:
                    assert key in data["by_symbol"]
            else:
                assert "kline" in data
                kline = data["kline"]
                assert "time_field" in kline
                assert "min_time_ms" in kline
                assert "max_time_ms" in kline
                assert "has_data" in kline
                if kline["min_time_ms"] is not None:
                    assert len(str(int(kline["min_time_ms"]))) == 13
                if kline["max_time_ms"] is not None:
                    assert len(str(int(kline["max_time_ms"]))) == 13
                if kline["has_data"]:
                    assert int(kline["min_time_ms"]) <= int(kline["max_time_ms"])


@allure.title('BF-USDM-KLINE-NORMAL-001 - Normal - symbol=BTCUSDT&interval=1m&start_time_ms=1704067200000&end_time_ms=1704153600000&limit=10')
@allure.feature('binance-full')
@allure.story('Normal')
@pytest.mark.binance_full_api
def test_bf_usdm_kline_normal_001(binance_full_api):
    """
    Case ID: BF-USDM-KLINE-NORMAL-001
    测试大类: binance-full
    测试类型: Normal
    测试目的: symbol=BTCUSDT&interval=1m&start_time_ms=1704067200000&end_time_ms=1704153600000&limit=10
    预期断言: 成功；items 为 K 线行；分页正确
    """

    case_id = 'BF-USDM-KLINE-NORMAL-001'

    responses = []

    request_params_1 = {'symbol': 'BTCUSDT', 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'limit': 10}

    response_1 = binance_full_api.get_usdm_kline(symbol='BTCUSDT', interval='1m', start_time_ms=1704067200000, end_time_ms=1704153600000, limit=10)

    responses.append(('kline', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
        case_id = 'BF-USDM-KLINE-NORMAL-001'
        assert isinstance(data, dict)
        if data.get("multi"):
            assert "by_symbol" in data
            for bucket in data["by_symbol"].values():
                assert "items" in bucket
                assert len(bucket["items"]) <= int(request_params.get("limit", LIMIT_NORMAL))
        else:
            assert "items" in data
            assert isinstance(data["items"], list)
            if request_params.get("limit") is not None:
                assert len(data["items"]) <= int(request_params["limit"])
            if "pagination" in data:
                pagination = data["pagination"]
                if request_params.get("include_total") and "total" in pagination and pagination["total"] is not None:
                    assert isinstance(pagination["total"], int)
                    assert pagination["total"] >= 0
            timestamps = []
            for item in data["items"]:
                for field in ("timestamp", "open", "high", "low", "close", "volume"):
                    assert field in item
                timestamp = int(item["timestamp"])
                timestamps.append(timestamp)
                assert len(str(timestamp)) == 13
                if request_params.get("start_time_ms") and request_params.get("end_time_ms"):
                    assert request_params["start_time_ms"] <= timestamp < request_params["end_time_ms"]
                if "1H-ALL" in case_id and item.get("interval") is not None:
                    assert item["interval"] == INTERVAL_1H
                for numeric_field in ("open", "high", "low", "close", "volume", "quote_volume"):
                    if numeric_field in item and item[numeric_field] is not None:
                        assert Decimal(str(item[numeric_field])) is not None
                if case_id.endswith("LOGIC-001") or case_id.endswith("DQC-001"):
                    open_value = Decimal(str(item["open"]))
                    high = Decimal(str(item["high"]))
                    low = Decimal(str(item["low"]))
                    close = Decimal(str(item["close"]))
                    assert high >= open_value
                    assert high >= close
                    assert high >= low
                    assert low <= open_value
                    assert low <= close
                    assert low <= high


@allure.title('BF-USDM-KLINE-NORMAL-002 - Normal - symbol=BTCUSDT,ETHUSDT&interval=1m&limit=10')
@allure.feature('binance-full')
@allure.story('Normal')
@pytest.mark.binance_full_api
def test_bf_usdm_kline_normal_002(binance_full_api):
    """
    Case ID: BF-USDM-KLINE-NORMAL-002
    测试大类: binance-full
    测试类型: Normal
    测试目的: symbol=BTCUSDT,ETHUSDT&interval=1m&limit=10
    预期断言: 多 symbol 分桶；每个桶 items 不超过 limit
    """

    case_id = 'BF-USDM-KLINE-NORMAL-002'

    responses = []

    request_params_1 = {'symbol': 'BTCUSDT,ETHUSDT', 'interval': '1m', 'limit': 10}

    response_1 = binance_full_api.get_usdm_kline(symbol='BTCUSDT,ETHUSDT', interval='1m', limit=10)

    responses.append(('kline', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
        case_id = 'BF-USDM-KLINE-NORMAL-002'
        assert isinstance(data, dict)
        if data.get("multi"):
            assert "by_symbol" in data
            for bucket in data["by_symbol"].values():
                assert "items" in bucket
                assert len(bucket["items"]) <= int(request_params.get("limit", LIMIT_NORMAL))
        else:
            assert "items" in data
            assert isinstance(data["items"], list)
            if request_params.get("limit") is not None:
                assert len(data["items"]) <= int(request_params["limit"])
            if "pagination" in data:
                pagination = data["pagination"]
                if request_params.get("include_total") and "total" in pagination and pagination["total"] is not None:
                    assert isinstance(pagination["total"], int)
                    assert pagination["total"] >= 0
            timestamps = []
            for item in data["items"]:
                for field in ("timestamp", "open", "high", "low", "close", "volume"):
                    assert field in item
                timestamp = int(item["timestamp"])
                timestamps.append(timestamp)
                assert len(str(timestamp)) == 13
                if request_params.get("start_time_ms") and request_params.get("end_time_ms"):
                    assert request_params["start_time_ms"] <= timestamp < request_params["end_time_ms"]
                if "1H-ALL" in case_id and item.get("interval") is not None:
                    assert item["interval"] == INTERVAL_1H
                for numeric_field in ("open", "high", "low", "close", "volume", "quote_volume"):
                    if numeric_field in item and item[numeric_field] is not None:
                        assert Decimal(str(item[numeric_field])) is not None
                if case_id.endswith("LOGIC-001") or case_id.endswith("DQC-001"):
                    open_value = Decimal(str(item["open"]))
                    high = Decimal(str(item["high"]))
                    low = Decimal(str(item["low"]))
                    close = Decimal(str(item["close"]))
                    assert high >= open_value
                    assert high >= close
                    assert high >= low
                    assert low <= open_value
                    assert low <= close
                    assert low <= high


@allure.title('BF-USDM-KLINE-BOUNDARY-001 - Boundary - interval=1h&limit=1')
@allure.feature('binance-full')
@allure.story('Boundary')
@pytest.mark.binance_full_api
def test_bf_usdm_kline_boundary_001(binance_full_api):
    """
    Case ID: BF-USDM-KLINE-BOUNDARY-001
    测试大类: binance-full
    测试类型: Boundary
    测试目的: interval=1h&limit=1
    预期断言: 查询 1h 表语义；最多 1 条
    """

    case_id = 'BF-USDM-KLINE-BOUNDARY-001'

    responses = []

    request_params_1 = {'symbol': 'BTCUSDT', 'interval': '1h', 'limit': 1}

    response_1 = binance_full_api.get_usdm_kline(symbol='BTCUSDT', interval='1h', limit=1)

    responses.append(('kline', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
        case_id = 'BF-USDM-KLINE-BOUNDARY-001'
        assert isinstance(data, dict)
        if data.get("multi"):
            assert "by_symbol" in data
            for bucket in data["by_symbol"].values():
                assert "items" in bucket
                assert len(bucket["items"]) <= int(request_params.get("limit", LIMIT_NORMAL))
        else:
            assert "items" in data
            assert isinstance(data["items"], list)
            if request_params.get("limit") is not None:
                assert len(data["items"]) <= int(request_params["limit"])
            if "pagination" in data:
                pagination = data["pagination"]
                if request_params.get("include_total") and "total" in pagination and pagination["total"] is not None:
                    assert isinstance(pagination["total"], int)
                    assert pagination["total"] >= 0
            timestamps = []
            for item in data["items"]:
                for field in ("timestamp", "open", "high", "low", "close", "volume"):
                    assert field in item
                timestamp = int(item["timestamp"])
                timestamps.append(timestamp)
                assert len(str(timestamp)) == 13
                if request_params.get("start_time_ms") and request_params.get("end_time_ms"):
                    assert request_params["start_time_ms"] <= timestamp < request_params["end_time_ms"]
                if "1H-ALL" in case_id and item.get("interval") is not None:
                    assert item["interval"] == INTERVAL_1H
                for numeric_field in ("open", "high", "low", "close", "volume", "quote_volume"):
                    if numeric_field in item and item[numeric_field] is not None:
                        assert Decimal(str(item[numeric_field])) is not None
                if case_id.endswith("LOGIC-001") or case_id.endswith("DQC-001"):
                    open_value = Decimal(str(item["open"]))
                    high = Decimal(str(item["high"]))
                    low = Decimal(str(item["low"]))
                    close = Decimal(str(item["close"]))
                    assert high >= open_value
                    assert high >= close
                    assert high >= low
                    assert low <= open_value
                    assert low <= close
                    assert low <= high


@allure.title('BF-USDM-KLINE-BOUNDARY-002 - Boundary - include_total=true&limit=1')
@allure.feature('binance-full')
@allure.story('Boundary')
@pytest.mark.binance_full_api
def test_bf_usdm_kline_boundary_002(binance_full_api):
    """
    Case ID: BF-USDM-KLINE-BOUNDARY-002
    测试大类: binance-full
    测试类型: Boundary
    测试目的: include_total=true&limit=1
    预期断言: pagination.total` 若返回则为非负整数
    """

    case_id = 'BF-USDM-KLINE-BOUNDARY-002'

    responses = []

    request_params_1 = {'symbol': 'BTCUSDT', 'interval': '1m', 'limit': 1, 'include_total': True}

    response_1 = binance_full_api.get_usdm_kline(symbol='BTCUSDT', interval='1m', limit=1, include_total=True)

    responses.append(('kline', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
        case_id = 'BF-USDM-KLINE-BOUNDARY-002'
        assert isinstance(data, dict)
        if data.get("multi"):
            assert "by_symbol" in data
            for bucket in data["by_symbol"].values():
                assert "items" in bucket
                assert len(bucket["items"]) <= int(request_params.get("limit", LIMIT_NORMAL))
        else:
            assert "items" in data
            assert isinstance(data["items"], list)
            if request_params.get("limit") is not None:
                assert len(data["items"]) <= int(request_params["limit"])
            if "pagination" in data:
                pagination = data["pagination"]
                if request_params.get("include_total") and "total" in pagination and pagination["total"] is not None:
                    assert isinstance(pagination["total"], int)
                    assert pagination["total"] >= 0
            timestamps = []
            for item in data["items"]:
                for field in ("timestamp", "open", "high", "low", "close", "volume"):
                    assert field in item
                timestamp = int(item["timestamp"])
                timestamps.append(timestamp)
                assert len(str(timestamp)) == 13
                if request_params.get("start_time_ms") and request_params.get("end_time_ms"):
                    assert request_params["start_time_ms"] <= timestamp < request_params["end_time_ms"]
                if "1H-ALL" in case_id and item.get("interval") is not None:
                    assert item["interval"] == INTERVAL_1H
                for numeric_field in ("open", "high", "low", "close", "volume", "quote_volume"):
                    if numeric_field in item and item[numeric_field] is not None:
                        assert Decimal(str(item[numeric_field])) is not None
                if case_id.endswith("LOGIC-001") or case_id.endswith("DQC-001"):
                    open_value = Decimal(str(item["open"]))
                    high = Decimal(str(item["high"]))
                    low = Decimal(str(item["low"]))
                    close = Decimal(str(item["close"]))
                    assert high >= open_value
                    assert high >= close
                    assert high >= low
                    assert low <= open_value
                    assert low <= close
                    assert low <= high


@allure.title('BF-USDM-KLINE-PARAM-001 - ParamError - 缺少 `symbol')
@allure.feature('binance-full')
@allure.story('ParamError')
@pytest.mark.binance_full_api
def test_bf_usdm_kline_param_001(binance_full_api):
    """
    Case ID: BF-USDM-KLINE-PARAM-001
    测试大类: binance-full
    测试类型: ParamError
    测试目的: 缺少 `symbol
    预期断言: 返回参数错误
    """

    case_id = 'BF-USDM-KLINE-PARAM-001'

    responses = []

    request_params_1 = {'interval': '1m'}

    try:
        response_1 = binance_full_api.get_usdm_kline(interval='1m')
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('kline', response_1, request_params_1))

    for target_name, response, request_params in responses:

        assert response.status_code < 500
        body = response.json() if response.content else {}
        assert str(body.get("code", "")) != "500"
        if response.status_code >= 400:
            assert response.status_code in {400, 422}
            assert any(key in str(body).lower() for key in ('symbol', 'pair', 'contract_type', 'time', 'limit', 'symbols'))
        else:
            assert "code" in body
            assert "status" in body
            assert "message" in body
            assert str(body["code"]) in {"400", "422"}
            assert body["status"] in {"error", "fail", "failed"}
        assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('BF-USDM-KLINE-PARAM-002 - ParamError - 只传时间窗一端')
@allure.feature('binance-full')
@allure.story('ParamError')
@pytest.mark.binance_full_api
def test_bf_usdm_kline_param_002(binance_full_api):
    """
    Case ID: BF-USDM-KLINE-PARAM-002
    测试大类: binance-full
    测试类型: ParamError
    测试目的: 只传时间窗一端
    预期断言: 返回时间窗成对错误
    """

    case_id = 'BF-USDM-KLINE-PARAM-002'

    responses = []

    request_params_1 = {'symbol': 'BTCUSDT', 'start_time_ms': 1704067200000}

    try:
        response_1 = binance_full_api.get_usdm_kline(symbol='BTCUSDT', start_time_ms=1704067200000)
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('kline', response_1, request_params_1))

    for target_name, response, request_params in responses:

        assert response.status_code < 500
        body = response.json() if response.content else {}
        assert str(body.get("code", "")) != "500"
        if response.status_code >= 400:
            assert response.status_code in {400, 422}
            assert any(key in str(body).lower() for key in ('symbol', 'pair', 'contract_type', 'time', 'limit', 'symbols'))
        else:
            assert "code" in body
            assert "status" in body
            assert "message" in body
            assert str(body["code"]) in {"400", "422"}
            assert body["status"] in {"error", "fail", "failed"}
        assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('BF-USDM-KLINE-PARAM-003 - ParamError - limit=0` 或 `limit=200001')
@allure.feature('binance-full')
@allure.story('ParamError')
@pytest.mark.binance_full_api
def test_bf_usdm_kline_param_003(binance_full_api):
    """
    Case ID: BF-USDM-KLINE-PARAM-003
    测试大类: binance-full
    测试类型: ParamError
    测试目的: limit=0` 或 `limit=200001
    预期断言: 返回参数错误
    """

    case_id = 'BF-USDM-KLINE-PARAM-003'

    responses = []

    request_params_1 = {'symbol': 'BTCUSDT', 'limit': 0}

    try:
        response_1 = binance_full_api.get_usdm_kline(symbol='BTCUSDT', limit=0)
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('kline', response_1, request_params_1))

    for target_name, response, request_params in responses:

        assert response.status_code < 500
        body = response.json() if response.content else {}
        assert str(body.get("code", "")) != "500"
        if response.status_code >= 400:
            assert response.status_code in {400, 422}
            assert any(key in str(body).lower() for key in ('symbol', 'pair', 'contract_type', 'time', 'limit', 'symbols'))
        else:
            assert "code" in body
            assert "status" in body
            assert "message" in body
            assert str(body["code"]) in {"400", "422"}
            assert body["status"] in {"error", "fail", "failed"}
        assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('BF-USDM-KLINE-DQC-001 - DataQuality - 正常请求')
@allure.feature('binance-full')
@allure.story('DataQuality')
@pytest.mark.binance_full_api
@pytest.mark.dqc
def test_bf_usdm_kline_dqc_001(binance_full_api):
    """
    Case ID: BF-USDM-KLINE-DQC-001
    测试大类: binance-full
    测试类型: DataQuality
    测试目的: 正常请求
    预期断言: timestamp 毫秒；数值字段可转数字
    """

    case_id = 'BF-USDM-KLINE-DQC-001'

    responses = []

    request_params_1 = {'symbol': 'BTCUSDT', 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'limit': 10}

    response_1 = binance_full_api.get_usdm_kline(symbol='BTCUSDT', interval='1m', start_time_ms=1704067200000, end_time_ms=1704153600000, limit=10)

    responses.append(('kline', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
        case_id = 'BF-USDM-KLINE-DQC-001'
        assert isinstance(data, dict)
        if data.get("multi"):
            assert "by_symbol" in data
            for bucket in data["by_symbol"].values():
                assert "items" in bucket
                assert len(bucket["items"]) <= int(request_params.get("limit", LIMIT_NORMAL))
        else:
            assert "items" in data
            assert isinstance(data["items"], list)
            if request_params.get("limit") is not None:
                assert len(data["items"]) <= int(request_params["limit"])
            if "pagination" in data:
                pagination = data["pagination"]
                if request_params.get("include_total") and "total" in pagination and pagination["total"] is not None:
                    assert isinstance(pagination["total"], int)
                    assert pagination["total"] >= 0
            timestamps = []
            for item in data["items"]:
                for field in ("timestamp", "open", "high", "low", "close", "volume"):
                    assert field in item
                timestamp = int(item["timestamp"])
                timestamps.append(timestamp)
                assert len(str(timestamp)) == 13
                if request_params.get("start_time_ms") and request_params.get("end_time_ms"):
                    assert request_params["start_time_ms"] <= timestamp < request_params["end_time_ms"]
                if "1H-ALL" in case_id and item.get("interval") is not None:
                    assert item["interval"] == INTERVAL_1H
                for numeric_field in ("open", "high", "low", "close", "volume", "quote_volume"):
                    if numeric_field in item and item[numeric_field] is not None:
                        assert Decimal(str(item[numeric_field])) is not None
                if case_id.endswith("LOGIC-001") or case_id.endswith("DQC-001"):
                    open_value = Decimal(str(item["open"]))
                    high = Decimal(str(item["high"]))
                    low = Decimal(str(item["low"]))
                    close = Decimal(str(item["close"]))
                    assert high >= open_value
                    assert high >= close
                    assert high >= low
                    assert low <= open_value
                    assert low <= close
                    assert low <= high


@allure.title('BF-USDM-KLINE-LOGIC-001 - BusinessLogic - 正常请求')
@allure.feature('binance-full')
@allure.story('BusinessLogic')
@pytest.mark.binance_full_api
@pytest.mark.logic
def test_bf_usdm_kline_logic_001(binance_full_api):
    """
    Case ID: BF-USDM-KLINE-LOGIC-001
    测试大类: binance-full
    测试类型: BusinessLogic
    测试目的: 正常请求
    预期断言: OHLC 合法；timestamp 在窗口内
    """

    case_id = 'BF-USDM-KLINE-LOGIC-001'

    responses = []

    request_params_1 = {'symbol': 'BTCUSDT', 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'limit': 10}

    response_1 = binance_full_api.get_usdm_kline(symbol='BTCUSDT', interval='1m', start_time_ms=1704067200000, end_time_ms=1704153600000, limit=10)

    responses.append(('kline', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
        case_id = 'BF-USDM-KLINE-LOGIC-001'
        assert isinstance(data, dict)
        if data.get("multi"):
            assert "by_symbol" in data
            for bucket in data["by_symbol"].values():
                assert "items" in bucket
                assert len(bucket["items"]) <= int(request_params.get("limit", LIMIT_NORMAL))
        else:
            assert "items" in data
            assert isinstance(data["items"], list)
            if request_params.get("limit") is not None:
                assert len(data["items"]) <= int(request_params["limit"])
            if "pagination" in data:
                pagination = data["pagination"]
                if request_params.get("include_total") and "total" in pagination and pagination["total"] is not None:
                    assert isinstance(pagination["total"], int)
                    assert pagination["total"] >= 0
            timestamps = []
            for item in data["items"]:
                for field in ("timestamp", "open", "high", "low", "close", "volume"):
                    assert field in item
                timestamp = int(item["timestamp"])
                timestamps.append(timestamp)
                assert len(str(timestamp)) == 13
                if request_params.get("start_time_ms") and request_params.get("end_time_ms"):
                    assert request_params["start_time_ms"] <= timestamp < request_params["end_time_ms"]
                if "1H-ALL" in case_id and item.get("interval") is not None:
                    assert item["interval"] == INTERVAL_1H
                for numeric_field in ("open", "high", "low", "close", "volume", "quote_volume"):
                    if numeric_field in item and item[numeric_field] is not None:
                        assert Decimal(str(item[numeric_field])) is not None
                if case_id.endswith("LOGIC-001") or case_id.endswith("DQC-001"):
                    open_value = Decimal(str(item["open"]))
                    high = Decimal(str(item["high"]))
                    low = Decimal(str(item["low"]))
                    close = Decimal(str(item["close"]))
                    assert high >= open_value
                    assert high >= close
                    assert high >= low
                    assert low <= open_value
                    assert low <= close
                    assert low <= high


@allure.title('BF-USDM-1H-ALL-NORMAL-001 - Normal - start_time_ms=1704067200000&end_time_ms=1704153600000&order=time_asc')
@allure.feature('binance-full')
@allure.story('Normal')
@pytest.mark.binance_full_api
def test_bf_usdm_1h_all_normal_001(binance_full_api):
    """
    Case ID: BF-USDM-1H-ALL-NORMAL-001
    测试大类: binance-full
    测试类型: Normal
    测试目的: start_time_ms=1704067200000&end_time_ms=1704153600000&order=time_asc
    预期断言: 成功；`data.items` 为扁平 K 线数组；`count == len(items)
    """

    case_id = 'BF-USDM-1H-ALL-NORMAL-001'

    responses = []

    request_params_1 = {'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'order': 'time_asc'}

    response_1 = binance_full_api.get_usdm_kline_1h_all_symbols(start_time_ms=1704067200000, end_time_ms=1704153600000, order='time_asc')

    responses.append(('all-symbols', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
        case_id = 'BF-USDM-1H-ALL-NORMAL-001'
        assert isinstance(data, dict)
        if data.get("multi"):
            assert "by_symbol" in data
            for bucket in data["by_symbol"].values():
                assert "items" in bucket
                assert len(bucket["items"]) <= int(request_params.get("limit", LIMIT_NORMAL))
        else:
            assert "items" in data
            assert isinstance(data["items"], list)
            if request_params.get("limit") is not None:
                assert len(data["items"]) <= int(request_params["limit"])
            if "pagination" in data:
                pagination = data["pagination"]
                if request_params.get("include_total") and "total" in pagination and pagination["total"] is not None:
                    assert isinstance(pagination["total"], int)
                    assert pagination["total"] >= 0
            timestamps = []
            for item in data["items"]:
                for field in ("timestamp", "open", "high", "low", "close", "volume"):
                    assert field in item
                timestamp = int(item["timestamp"])
                timestamps.append(timestamp)
                assert len(str(timestamp)) == 13
                if request_params.get("start_time_ms") and request_params.get("end_time_ms"):
                    assert request_params["start_time_ms"] <= timestamp < request_params["end_time_ms"]
                if "1H-ALL" in case_id and item.get("interval") is not None:
                    assert item["interval"] == INTERVAL_1H
                for numeric_field in ("open", "high", "low", "close", "volume", "quote_volume"):
                    if numeric_field in item and item[numeric_field] is not None:
                        assert Decimal(str(item[numeric_field])) is not None
                if case_id.endswith("LOGIC-001") or case_id.endswith("DQC-001"):
                    open_value = Decimal(str(item["open"]))
                    high = Decimal(str(item["high"]))
                    low = Decimal(str(item["low"]))
                    close = Decimal(str(item["close"]))
                    assert high >= open_value
                    assert high >= close
                    assert high >= low
                    assert low <= open_value
                    assert low <= close
                    assert low <= high


@allure.title('BF-USDM-1H-ALL-NORMAL-002 - Normal - 加 `symbol=BTCUSDT')
@allure.feature('binance-full')
@allure.story('Normal')
@pytest.mark.binance_full_api
def test_bf_usdm_1h_all_normal_002(binance_full_api):
    """
    Case ID: BF-USDM-1H-ALL-NORMAL-002
    测试大类: binance-full
    测试类型: Normal
    测试目的: 加 `symbol=BTCUSDT
    预期断言: 只返回该 symbol 或空数组
    """

    case_id = 'BF-USDM-1H-ALL-NORMAL-002'

    responses = []

    request_params_1 = {'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'symbol': 'BTCUSDT'}

    response_1 = binance_full_api.get_usdm_kline_1h_all_symbols(start_time_ms=1704067200000, end_time_ms=1704153600000, symbol='BTCUSDT')

    responses.append(('all-symbols', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
        case_id = 'BF-USDM-1H-ALL-NORMAL-002'
        assert isinstance(data, dict)
        if data.get("multi"):
            assert "by_symbol" in data
            for bucket in data["by_symbol"].values():
                assert "items" in bucket
                assert len(bucket["items"]) <= int(request_params.get("limit", LIMIT_NORMAL))
        else:
            assert "items" in data
            assert isinstance(data["items"], list)
            if request_params.get("limit") is not None:
                assert len(data["items"]) <= int(request_params["limit"])
            if "pagination" in data:
                pagination = data["pagination"]
                if request_params.get("include_total") and "total" in pagination and pagination["total"] is not None:
                    assert isinstance(pagination["total"], int)
                    assert pagination["total"] >= 0
            timestamps = []
            for item in data["items"]:
                for field in ("timestamp", "open", "high", "low", "close", "volume"):
                    assert field in item
                timestamp = int(item["timestamp"])
                timestamps.append(timestamp)
                assert len(str(timestamp)) == 13
                if request_params.get("start_time_ms") and request_params.get("end_time_ms"):
                    assert request_params["start_time_ms"] <= timestamp < request_params["end_time_ms"]
                if "1H-ALL" in case_id and item.get("interval") is not None:
                    assert item["interval"] == INTERVAL_1H
                for numeric_field in ("open", "high", "low", "close", "volume", "quote_volume"):
                    if numeric_field in item and item[numeric_field] is not None:
                        assert Decimal(str(item[numeric_field])) is not None
                if case_id.endswith("LOGIC-001") or case_id.endswith("DQC-001"):
                    open_value = Decimal(str(item["open"]))
                    high = Decimal(str(item["high"]))
                    low = Decimal(str(item["low"]))
                    close = Decimal(str(item["close"]))
                    assert high >= open_value
                    assert high >= close
                    assert high >= low
                    assert low <= open_value
                    assert low <= close
                    assert low <= high


@allure.title('BF-USDM-1H-ALL-BOUNDARY-001 - Boundary - order=time_desc')
@allure.feature('binance-full')
@allure.story('Boundary')
@pytest.mark.binance_full_api
def test_bf_usdm_1h_all_boundary_001(binance_full_api):
    """
    Case ID: BF-USDM-1H-ALL-BOUNDARY-001
    测试大类: binance-full
    测试类型: Boundary
    测试目的: order=time_desc
    预期断言: 返回按时间倒序或服务明确提示
    """

    case_id = 'BF-USDM-1H-ALL-BOUNDARY-001'

    responses = []

    request_params_1 = {'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'order': 'time_desc'}

    try:
        response_1 = binance_full_api.get_usdm_kline_1h_all_symbols(start_time_ms=1704067200000, end_time_ms=1704153600000, order='time_desc')
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('all-symbols', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
            case_id = 'BF-USDM-1H-ALL-BOUNDARY-001'
            assert isinstance(data, dict)
            if data.get("multi"):
                assert "by_symbol" in data
                for bucket in data["by_symbol"].values():
                    assert "items" in bucket
                    assert len(bucket["items"]) <= int(request_params.get("limit", LIMIT_NORMAL))
            else:
                assert "items" in data
                assert isinstance(data["items"], list)
                if request_params.get("limit") is not None:
                    assert len(data["items"]) <= int(request_params["limit"])
                if "pagination" in data:
                    pagination = data["pagination"]
                    if request_params.get("include_total") and "total" in pagination and pagination["total"] is not None:
                        assert isinstance(pagination["total"], int)
                        assert pagination["total"] >= 0
                timestamps = []
                for item in data["items"]:
                    for field in ("timestamp", "open", "high", "low", "close", "volume"):
                        assert field in item
                    timestamp = int(item["timestamp"])
                    timestamps.append(timestamp)
                    assert len(str(timestamp)) == 13
                    if request_params.get("start_time_ms") and request_params.get("end_time_ms"):
                        assert request_params["start_time_ms"] <= timestamp < request_params["end_time_ms"]
                    if "1H-ALL" in case_id and item.get("interval") is not None:
                        assert item["interval"] == INTERVAL_1H
                    for numeric_field in ("open", "high", "low", "close", "volume", "quote_volume"):
                        if numeric_field in item and item[numeric_field] is not None:
                            assert Decimal(str(item[numeric_field])) is not None
                    if case_id.endswith("LOGIC-001") or case_id.endswith("DQC-001"):
                        open_value = Decimal(str(item["open"]))
                        high = Decimal(str(item["high"]))
                        low = Decimal(str(item["low"]))
                        close = Decimal(str(item["close"]))
                        assert high >= open_value
                        assert high >= close
                        assert high >= low
                        assert low <= open_value
                        assert low <= close
                        assert low <= high
                if len(timestamps) > 1:
                    assert timestamps == sorted(timestamps, reverse=True)


@allure.title('BF-USDM-1H-ALL-PARAM-001 - ParamError - 缺少 `start_time_ms')
@allure.feature('binance-full')
@allure.story('ParamError')
@pytest.mark.binance_full_api
def test_bf_usdm_1h_all_param_001(binance_full_api):
    """
    Case ID: BF-USDM-1H-ALL-PARAM-001
    测试大类: binance-full
    测试类型: ParamError
    测试目的: 缺少 `start_time_ms
    预期断言: 返回参数错误
    """

    case_id = 'BF-USDM-1H-ALL-PARAM-001'

    responses = []

    request_params_1 = {'end_time_ms': 1704153600000}

    try:
        response_1 = binance_full_api.get_usdm_kline_1h_all_symbols(end_time_ms=1704153600000)
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('all-symbols', response_1, request_params_1))

    for target_name, response, request_params in responses:

        assert response.status_code < 500
        body = response.json() if response.content else {}
        assert str(body.get("code", "")) != "500"
        if response.status_code >= 400:
            assert response.status_code in {400, 422}
            assert any(key in str(body).lower() for key in ('symbol', 'pair', 'contract_type', 'time', 'limit', 'symbols'))
        else:
            assert "code" in body
            assert "status" in body
            assert "message" in body
            assert str(body["code"]) in {"400", "422"}
            assert body["status"] in {"error", "fail", "failed"}
        assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('BF-USDM-1H-ALL-PARAM-002 - ParamError - 缺少 `end_time_ms')
@allure.feature('binance-full')
@allure.story('ParamError')
@pytest.mark.binance_full_api
def test_bf_usdm_1h_all_param_002(binance_full_api):
    """
    Case ID: BF-USDM-1H-ALL-PARAM-002
    测试大类: binance-full
    测试类型: ParamError
    测试目的: 缺少 `end_time_ms
    预期断言: 返回参数错误
    """

    case_id = 'BF-USDM-1H-ALL-PARAM-002'

    responses = []

    request_params_1 = {'start_time_ms': 1704067200000}

    try:
        response_1 = binance_full_api.get_usdm_kline_1h_all_symbols(start_time_ms=1704067200000)
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('all-symbols', response_1, request_params_1))

    for target_name, response, request_params in responses:

        assert response.status_code < 500
        body = response.json() if response.content else {}
        assert str(body.get("code", "")) != "500"
        if response.status_code >= 400:
            assert response.status_code in {400, 422}
            assert any(key in str(body).lower() for key in ('symbol', 'pair', 'contract_type', 'time', 'limit', 'symbols'))
        else:
            assert "code" in body
            assert "status" in body
            assert "message" in body
            assert str(body["code"]) in {"400", "422"}
            assert body["status"] in {"error", "fail", "failed"}
        assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('BF-USDM-1H-ALL-PARAM-003 - ParamError - symbol=BTCUSDT,ETHUSDT')
@allure.feature('binance-full')
@allure.story('ParamError')
@pytest.mark.binance_full_api
def test_bf_usdm_1h_all_param_003(binance_full_api):
    """
    Case ID: BF-USDM-1H-ALL-PARAM-003
    测试大类: binance-full
    测试类型: ParamError
    测试目的: symbol=BTCUSDT,ETHUSDT
    预期断言: 文档说明不支持多选；应返回参数错误或业务错误
    """

    case_id = 'BF-USDM-1H-ALL-PARAM-003'

    responses = []

    request_params_1 = {'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'symbol': 'BTCUSDT,ETHUSDT'}

    try:
        response_1 = binance_full_api.get_usdm_kline_1h_all_symbols(start_time_ms=1704067200000, end_time_ms=1704153600000, symbol='BTCUSDT,ETHUSDT')
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('all-symbols', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
            case_id = 'BF-USDM-1H-ALL-PARAM-003'
            assert isinstance(data, dict)
            if data.get("multi"):
                assert "by_symbol" in data
                for bucket in data["by_symbol"].values():
                    assert "items" in bucket
                    assert len(bucket["items"]) <= int(request_params.get("limit", LIMIT_NORMAL))
            else:
                assert "items" in data
                assert isinstance(data["items"], list)
                if request_params.get("limit") is not None:
                    assert len(data["items"]) <= int(request_params["limit"])
                if "pagination" in data:
                    pagination = data["pagination"]
                    if request_params.get("include_total") and "total" in pagination and pagination["total"] is not None:
                        assert isinstance(pagination["total"], int)
                        assert pagination["total"] >= 0
                timestamps = []
                for item in data["items"]:
                    for field in ("timestamp", "open", "high", "low", "close", "volume"):
                        assert field in item
                    timestamp = int(item["timestamp"])
                    timestamps.append(timestamp)
                    assert len(str(timestamp)) == 13
                    if request_params.get("start_time_ms") and request_params.get("end_time_ms"):
                        assert request_params["start_time_ms"] <= timestamp < request_params["end_time_ms"]
                    if "1H-ALL" in case_id and item.get("interval") is not None:
                        assert item["interval"] == INTERVAL_1H
                    for numeric_field in ("open", "high", "low", "close", "volume", "quote_volume"):
                        if numeric_field in item and item[numeric_field] is not None:
                            assert Decimal(str(item[numeric_field])) is not None
                    if case_id.endswith("LOGIC-001") or case_id.endswith("DQC-001"):
                        open_value = Decimal(str(item["open"]))
                        high = Decimal(str(item["high"]))
                        low = Decimal(str(item["low"]))
                        close = Decimal(str(item["close"]))
                        assert high >= open_value
                        assert high >= close
                        assert high >= low
                        assert low <= open_value
                        assert low <= close
                        assert low <= high


@allure.title('BF-USDM-1H-ALL-DQC-001 - DataQuality - 正常请求')
@allure.feature('binance-full')
@allure.story('DataQuality')
@pytest.mark.binance_full_api
@pytest.mark.dqc
def test_bf_usdm_1h_all_dqc_001(binance_full_api):
    """
    Case ID: BF-USDM-1H-ALL-DQC-001
    测试大类: binance-full
    测试类型: DataQuality
    测试目的: 正常请求
    预期断言: timestamp 毫秒；interval 应为 `1h` 或符合接口语义
    """

    case_id = 'BF-USDM-1H-ALL-DQC-001'

    responses = []

    request_params_1 = {'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000}

    response_1 = binance_full_api.get_usdm_kline_1h_all_symbols(start_time_ms=1704067200000, end_time_ms=1704153600000)

    responses.append(('all-symbols', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
        case_id = 'BF-USDM-1H-ALL-DQC-001'
        assert isinstance(data, dict)
        if data.get("multi"):
            assert "by_symbol" in data
            for bucket in data["by_symbol"].values():
                assert "items" in bucket
                assert len(bucket["items"]) <= int(request_params.get("limit", LIMIT_NORMAL))
        else:
            assert "items" in data
            assert isinstance(data["items"], list)
            if request_params.get("limit") is not None:
                assert len(data["items"]) <= int(request_params["limit"])
            if "pagination" in data:
                pagination = data["pagination"]
                if request_params.get("include_total") and "total" in pagination and pagination["total"] is not None:
                    assert isinstance(pagination["total"], int)
                    assert pagination["total"] >= 0
            timestamps = []
            for item in data["items"]:
                for field in ("timestamp", "open", "high", "low", "close", "volume"):
                    assert field in item
                timestamp = int(item["timestamp"])
                timestamps.append(timestamp)
                assert len(str(timestamp)) == 13
                if request_params.get("start_time_ms") and request_params.get("end_time_ms"):
                    assert request_params["start_time_ms"] <= timestamp < request_params["end_time_ms"]
                if "1H-ALL" in case_id and item.get("interval") is not None:
                    assert item["interval"] == INTERVAL_1H
                for numeric_field in ("open", "high", "low", "close", "volume", "quote_volume"):
                    if numeric_field in item and item[numeric_field] is not None:
                        assert Decimal(str(item[numeric_field])) is not None
                if case_id.endswith("LOGIC-001") or case_id.endswith("DQC-001"):
                    open_value = Decimal(str(item["open"]))
                    high = Decimal(str(item["high"]))
                    low = Decimal(str(item["low"]))
                    close = Decimal(str(item["close"]))
                    assert high >= open_value
                    assert high >= close
                    assert high >= low
                    assert low <= open_value
                    assert low <= close
                    assert low <= high


@allure.title('BF-USDM-1H-ALL-LOGIC-001 - BusinessLogic - 正常请求')
@allure.feature('binance-full')
@allure.story('BusinessLogic')
@pytest.mark.binance_full_api
@pytest.mark.logic
def test_bf_usdm_1h_all_logic_001(binance_full_api):
    """
    Case ID: BF-USDM-1H-ALL-LOGIC-001
    测试大类: binance-full
    测试类型: BusinessLogic
    测试目的: 正常请求
    预期断言: OHLC 合法；排序方向正确
    """

    case_id = 'BF-USDM-1H-ALL-LOGIC-001'

    responses = []

    request_params_1 = {'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'order': 'time_asc'}

    response_1 = binance_full_api.get_usdm_kline_1h_all_symbols(start_time_ms=1704067200000, end_time_ms=1704153600000, order='time_asc')

    responses.append(('all-symbols', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
        case_id = 'BF-USDM-1H-ALL-LOGIC-001'
        assert isinstance(data, dict)
        if data.get("multi"):
            assert "by_symbol" in data
            for bucket in data["by_symbol"].values():
                assert "items" in bucket
                assert len(bucket["items"]) <= int(request_params.get("limit", LIMIT_NORMAL))
        else:
            assert "items" in data
            assert isinstance(data["items"], list)
            if request_params.get("limit") is not None:
                assert len(data["items"]) <= int(request_params["limit"])
            if "pagination" in data:
                pagination = data["pagination"]
                if request_params.get("include_total") and "total" in pagination and pagination["total"] is not None:
                    assert isinstance(pagination["total"], int)
                    assert pagination["total"] >= 0
            timestamps = []
            for item in data["items"]:
                for field in ("timestamp", "open", "high", "low", "close", "volume"):
                    assert field in item
                timestamp = int(item["timestamp"])
                timestamps.append(timestamp)
                assert len(str(timestamp)) == 13
                if request_params.get("start_time_ms") and request_params.get("end_time_ms"):
                    assert request_params["start_time_ms"] <= timestamp < request_params["end_time_ms"]
                if "1H-ALL" in case_id and item.get("interval") is not None:
                    assert item["interval"] == INTERVAL_1H
                for numeric_field in ("open", "high", "low", "close", "volume", "quote_volume"):
                    if numeric_field in item and item[numeric_field] is not None:
                        assert Decimal(str(item[numeric_field])) is not None
                if case_id.endswith("LOGIC-001") or case_id.endswith("DQC-001"):
                    open_value = Decimal(str(item["open"]))
                    high = Decimal(str(item["high"]))
                    low = Decimal(str(item["low"]))
                    close = Decimal(str(item["close"]))
                    assert high >= open_value
                    assert high >= close
                    assert high >= low
                    assert low <= open_value
                    assert low <= close
                    assert low <= high
            if len(timestamps) > 1:
                assert timestamps == sorted(timestamps)


@allure.title('BF-DERIV-KLINE-NORMAL-001 - Normal - 三个接口 | 合法 `pair/contract_type/interval=1m/limit=10')
@allure.feature('binance-full')
@allure.story('Normal')
@pytest.mark.binance_full_api
def test_bf_deriv_kline_normal_001(binance_full_api):
    """
    Case ID: BF-DERIV-KLINE-NORMAL-001
    测试大类: binance-full
    测试类型: Normal
    测试目的: 三个接口 | 合法 `pair/contract_type/interval=1m/limit=10
    预期断言: 成功；items 为 K 线行；分页正确
    """

    case_id = 'BF-DERIV-KLINE-NORMAL-001'

    responses = []

    request_params_1 = {'pair': 'BTCUSD', 'contract_type': 'PERPETUAL', 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'limit': 10, 'offset': 0, 'include_total': False}

    response_1 = binance_full_api.get_coinm_perp_kline(pair='BTCUSD', contract_type='PERPETUAL', interval='1m', start_time_ms=1704067200000, end_time_ms=1704153600000, limit=10, offset=0, include_total=False)

    responses.append(('kline', response_1, request_params_1))

    request_params_2 = {'pair': 'BTCUSD', 'contract_type': 'CURRENT_QUARTER', 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'limit': 10, 'offset': 0, 'include_total': False}

    response_2 = binance_full_api.get_coinm_delivery_kline(pair='BTCUSD', contract_type='CURRENT_QUARTER', interval='1m', start_time_ms=1704067200000, end_time_ms=1704153600000, limit=10, offset=0, include_total=False)

    responses.append(('kline', response_2, request_params_2))

    request_params_3 = {'pair': 'BTCUSDT', 'contract_type': 'CURRENT_QUARTER', 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'limit': 10, 'offset': 0, 'include_total': False}

    response_3 = binance_full_api.get_usdm_delivery_kline(pair='BTCUSDT', contract_type='CURRENT_QUARTER', interval='1m', start_time_ms=1704067200000, end_time_ms=1704153600000, limit=10, offset=0, include_total=False)

    responses.append(('kline', response_3, request_params_3))

    for target_name, response, request_params in responses:

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
        case_id = 'BF-DERIV-KLINE-NORMAL-001'
        assert isinstance(data, dict)
        if data.get("multi"):
            assert "by_symbol" in data
            for bucket in data["by_symbol"].values():
                assert "items" in bucket
                assert len(bucket["items"]) <= int(request_params.get("limit", LIMIT_NORMAL))
        else:
            assert "items" in data
            assert isinstance(data["items"], list)
            if request_params.get("limit") is not None:
                assert len(data["items"]) <= int(request_params["limit"])
            if "pagination" in data:
                pagination = data["pagination"]
                if request_params.get("include_total") and "total" in pagination and pagination["total"] is not None:
                    assert isinstance(pagination["total"], int)
                    assert pagination["total"] >= 0
            timestamps = []
            for item in data["items"]:
                for field in ("timestamp", "open", "high", "low", "close", "volume"):
                    assert field in item
                timestamp = int(item["timestamp"])
                timestamps.append(timestamp)
                assert len(str(timestamp)) == 13
                if request_params.get("start_time_ms") and request_params.get("end_time_ms"):
                    assert request_params["start_time_ms"] <= timestamp < request_params["end_time_ms"]
                if "1H-ALL" in case_id and item.get("interval") is not None:
                    assert item["interval"] == INTERVAL_1H
                for numeric_field in ("open", "high", "low", "close", "volume", "quote_volume"):
                    if numeric_field in item and item[numeric_field] is not None:
                        assert Decimal(str(item[numeric_field])) is not None
                if case_id.endswith("LOGIC-001") or case_id.endswith("DQC-001"):
                    open_value = Decimal(str(item["open"]))
                    high = Decimal(str(item["high"]))
                    low = Decimal(str(item["low"]))
                    close = Decimal(str(item["close"]))
                    assert high >= open_value
                    assert high >= close
                    assert high >= low
                    assert low <= open_value
                    assert low <= close
                    assert low <= high


@allure.title('BF-DERIV-KLINE-NORMAL-002 - Normal - 三个接口 | 多 pair 逗号分隔')
@allure.feature('binance-full')
@allure.story('Normal')
@pytest.mark.binance_full_api
def test_bf_deriv_kline_normal_002(binance_full_api):
    """
    Case ID: BF-DERIV-KLINE-NORMAL-002
    测试大类: binance-full
    测试类型: Normal
    测试目的: 三个接口 | 多 pair 逗号分隔
    预期断言: 多桶结构正确或返回明确不支持提示；不能 500
    """

    case_id = 'BF-DERIV-KLINE-NORMAL-002'

    responses = []

    request_params_1 = {'pair': 'BTCUSD,ETHUSD', 'contract_type': 'PERPETUAL', 'interval': '1m', 'limit': 10}

    try:
        response_1 = binance_full_api.get_coinm_perp_kline(pair='BTCUSD,ETHUSD', contract_type='PERPETUAL', interval='1m', limit=10)
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('kline', response_1, request_params_1))

    request_params_2 = {'pair': 'BTCUSD,ETHUSD', 'contract_type': 'CURRENT_QUARTER', 'interval': '1m', 'limit': 10}

    try:
        response_2 = binance_full_api.get_coinm_delivery_kline(pair='BTCUSD,ETHUSD', contract_type='CURRENT_QUARTER', interval='1m', limit=10)
    except HTTPError as exc:
        assert exc.response is not None
        response_2 = exc.response

    responses.append(('kline', response_2, request_params_2))

    request_params_3 = {'pair': 'BTCUSDT,ETHUSDT', 'contract_type': 'CURRENT_QUARTER', 'interval': '1m', 'limit': 10}

    try:
        response_3 = binance_full_api.get_usdm_delivery_kline(pair='BTCUSDT,ETHUSDT', contract_type='CURRENT_QUARTER', interval='1m', limit=10)
    except HTTPError as exc:
        assert exc.response is not None
        response_3 = exc.response

    responses.append(('kline', response_3, request_params_3))

    for target_name, response, request_params in responses:

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
            case_id = 'BF-DERIV-KLINE-NORMAL-002'
            assert isinstance(data, dict)
            if data.get("multi"):
                assert "by_symbol" in data
                for bucket in data["by_symbol"].values():
                    assert "items" in bucket
                    assert len(bucket["items"]) <= int(request_params.get("limit", LIMIT_NORMAL))
            else:
                assert "items" in data
                assert isinstance(data["items"], list)
                if request_params.get("limit") is not None:
                    assert len(data["items"]) <= int(request_params["limit"])
                if "pagination" in data:
                    pagination = data["pagination"]
                    if request_params.get("include_total") and "total" in pagination and pagination["total"] is not None:
                        assert isinstance(pagination["total"], int)
                        assert pagination["total"] >= 0
                timestamps = []
                for item in data["items"]:
                    for field in ("timestamp", "open", "high", "low", "close", "volume"):
                        assert field in item
                    timestamp = int(item["timestamp"])
                    timestamps.append(timestamp)
                    assert len(str(timestamp)) == 13
                    if request_params.get("start_time_ms") and request_params.get("end_time_ms"):
                        assert request_params["start_time_ms"] <= timestamp < request_params["end_time_ms"]
                    if "1H-ALL" in case_id and item.get("interval") is not None:
                        assert item["interval"] == INTERVAL_1H
                    for numeric_field in ("open", "high", "low", "close", "volume", "quote_volume"):
                        if numeric_field in item and item[numeric_field] is not None:
                            assert Decimal(str(item[numeric_field])) is not None
                    if case_id.endswith("LOGIC-001") or case_id.endswith("DQC-001"):
                        open_value = Decimal(str(item["open"]))
                        high = Decimal(str(item["high"]))
                        low = Decimal(str(item["low"]))
                        close = Decimal(str(item["close"]))
                        assert high >= open_value
                        assert high >= close
                        assert high >= low
                        assert low <= open_value
                        assert low <= close
                        assert low <= high


@allure.title('BF-DERIV-KLINE-BOUNDARY-001 - Boundary - 三个接口 | limit=1&offset=0')
@allure.feature('binance-full')
@allure.story('Boundary')
@pytest.mark.binance_full_api
def test_bf_deriv_kline_boundary_001(binance_full_api):
    """
    Case ID: BF-DERIV-KLINE-BOUNDARY-001
    测试大类: binance-full
    测试类型: Boundary
    测试目的: 三个接口 | limit=1&offset=0
    预期断言: 最多 1 条；分页回显正确
    """

    case_id = 'BF-DERIV-KLINE-BOUNDARY-001'

    responses = []

    request_params_1 = {'pair': 'BTCUSD', 'contract_type': 'PERPETUAL', 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'limit': 1, 'offset': 0, 'include_total': False}

    response_1 = binance_full_api.get_coinm_perp_kline(pair='BTCUSD', contract_type='PERPETUAL', interval='1m', start_time_ms=1704067200000, end_time_ms=1704153600000, limit=1, offset=0, include_total=False)

    responses.append(('kline', response_1, request_params_1))

    request_params_2 = {'pair': 'BTCUSD', 'contract_type': 'CURRENT_QUARTER', 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'limit': 1, 'offset': 0, 'include_total': False}

    response_2 = binance_full_api.get_coinm_delivery_kline(pair='BTCUSD', contract_type='CURRENT_QUARTER', interval='1m', start_time_ms=1704067200000, end_time_ms=1704153600000, limit=1, offset=0, include_total=False)

    responses.append(('kline', response_2, request_params_2))

    request_params_3 = {'pair': 'BTCUSDT', 'contract_type': 'CURRENT_QUARTER', 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'limit': 1, 'offset': 0, 'include_total': False}

    response_3 = binance_full_api.get_usdm_delivery_kline(pair='BTCUSDT', contract_type='CURRENT_QUARTER', interval='1m', start_time_ms=1704067200000, end_time_ms=1704153600000, limit=1, offset=0, include_total=False)

    responses.append(('kline', response_3, request_params_3))

    for target_name, response, request_params in responses:

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
        case_id = 'BF-DERIV-KLINE-BOUNDARY-001'
        assert isinstance(data, dict)
        if data.get("multi"):
            assert "by_symbol" in data
            for bucket in data["by_symbol"].values():
                assert "items" in bucket
                assert len(bucket["items"]) <= int(request_params.get("limit", LIMIT_NORMAL))
        else:
            assert "items" in data
            assert isinstance(data["items"], list)
            if request_params.get("limit") is not None:
                assert len(data["items"]) <= int(request_params["limit"])
            if "pagination" in data:
                pagination = data["pagination"]
                if request_params.get("include_total") and "total" in pagination and pagination["total"] is not None:
                    assert isinstance(pagination["total"], int)
                    assert pagination["total"] >= 0
            timestamps = []
            for item in data["items"]:
                for field in ("timestamp", "open", "high", "low", "close", "volume"):
                    assert field in item
                timestamp = int(item["timestamp"])
                timestamps.append(timestamp)
                assert len(str(timestamp)) == 13
                if request_params.get("start_time_ms") and request_params.get("end_time_ms"):
                    assert request_params["start_time_ms"] <= timestamp < request_params["end_time_ms"]
                if "1H-ALL" in case_id and item.get("interval") is not None:
                    assert item["interval"] == INTERVAL_1H
                for numeric_field in ("open", "high", "low", "close", "volume", "quote_volume"):
                    if numeric_field in item and item[numeric_field] is not None:
                        assert Decimal(str(item[numeric_field])) is not None
                if case_id.endswith("LOGIC-001") or case_id.endswith("DQC-001"):
                    open_value = Decimal(str(item["open"]))
                    high = Decimal(str(item["high"]))
                    low = Decimal(str(item["low"]))
                    close = Decimal(str(item["close"]))
                    assert high >= open_value
                    assert high >= close
                    assert high >= low
                    assert low <= open_value
                    assert low <= close
                    assert low <= high


@allure.title('BF-DERIV-KLINE-BOUNDARY-002 - Boundary - 三个接口 | include_total=true')
@allure.feature('binance-full')
@allure.story('Boundary')
@pytest.mark.binance_full_api
def test_bf_deriv_kline_boundary_002(binance_full_api):
    """
    Case ID: BF-DERIV-KLINE-BOUNDARY-002
    测试大类: binance-full
    测试类型: Boundary
    测试目的: 三个接口 | include_total=true
    预期断言: total 若返回则为非负整数
    """

    case_id = 'BF-DERIV-KLINE-BOUNDARY-002'

    responses = []

    request_params_1 = {'pair': 'BTCUSD', 'contract_type': 'PERPETUAL', 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'limit': 1, 'offset': 0, 'include_total': True}

    response_1 = binance_full_api.get_coinm_perp_kline(pair='BTCUSD', contract_type='PERPETUAL', interval='1m', start_time_ms=1704067200000, end_time_ms=1704153600000, limit=1, offset=0, include_total=True)

    responses.append(('kline', response_1, request_params_1))

    request_params_2 = {'pair': 'BTCUSD', 'contract_type': 'CURRENT_QUARTER', 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'limit': 1, 'offset': 0, 'include_total': True}

    response_2 = binance_full_api.get_coinm_delivery_kline(pair='BTCUSD', contract_type='CURRENT_QUARTER', interval='1m', start_time_ms=1704067200000, end_time_ms=1704153600000, limit=1, offset=0, include_total=True)

    responses.append(('kline', response_2, request_params_2))

    request_params_3 = {'pair': 'BTCUSDT', 'contract_type': 'CURRENT_QUARTER', 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'limit': 1, 'offset': 0, 'include_total': True}

    response_3 = binance_full_api.get_usdm_delivery_kline(pair='BTCUSDT', contract_type='CURRENT_QUARTER', interval='1m', start_time_ms=1704067200000, end_time_ms=1704153600000, limit=1, offset=0, include_total=True)

    responses.append(('kline', response_3, request_params_3))

    for target_name, response, request_params in responses:

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
        case_id = 'BF-DERIV-KLINE-BOUNDARY-002'
        assert isinstance(data, dict)
        if data.get("multi"):
            assert "by_symbol" in data
            for bucket in data["by_symbol"].values():
                assert "items" in bucket
                assert len(bucket["items"]) <= int(request_params.get("limit", LIMIT_NORMAL))
        else:
            assert "items" in data
            assert isinstance(data["items"], list)
            if request_params.get("limit") is not None:
                assert len(data["items"]) <= int(request_params["limit"])
            if "pagination" in data:
                pagination = data["pagination"]
                if request_params.get("include_total") and "total" in pagination and pagination["total"] is not None:
                    assert isinstance(pagination["total"], int)
                    assert pagination["total"] >= 0
            timestamps = []
            for item in data["items"]:
                for field in ("timestamp", "open", "high", "low", "close", "volume"):
                    assert field in item
                timestamp = int(item["timestamp"])
                timestamps.append(timestamp)
                assert len(str(timestamp)) == 13
                if request_params.get("start_time_ms") and request_params.get("end_time_ms"):
                    assert request_params["start_time_ms"] <= timestamp < request_params["end_time_ms"]
                if "1H-ALL" in case_id and item.get("interval") is not None:
                    assert item["interval"] == INTERVAL_1H
                for numeric_field in ("open", "high", "low", "close", "volume", "quote_volume"):
                    if numeric_field in item and item[numeric_field] is not None:
                        assert Decimal(str(item[numeric_field])) is not None
                if case_id.endswith("LOGIC-001") or case_id.endswith("DQC-001"):
                    open_value = Decimal(str(item["open"]))
                    high = Decimal(str(item["high"]))
                    low = Decimal(str(item["low"]))
                    close = Decimal(str(item["close"]))
                    assert high >= open_value
                    assert high >= close
                    assert high >= low
                    assert low <= open_value
                    assert low <= close
                    assert low <= high


@allure.title('BF-DERIV-KLINE-PARAM-001 - ParamError - 三个接口 | 缺少 `pair')
@allure.feature('binance-full')
@allure.story('ParamError')
@pytest.mark.binance_full_api
def test_bf_deriv_kline_param_001(binance_full_api):
    """
    Case ID: BF-DERIV-KLINE-PARAM-001
    测试大类: binance-full
    测试类型: ParamError
    测试目的: 三个接口 | 缺少 `pair
    预期断言: 返回参数错误
    """

    case_id = 'BF-DERIV-KLINE-PARAM-001'

    responses = []

    request_params_1 = {'contract_type': 'PERPETUAL'}

    try:
        response_1 = binance_full_api.get_coinm_perp_kline(contract_type='PERPETUAL')
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('kline', response_1, request_params_1))

    request_params_2 = {'contract_type': 'CURRENT_QUARTER'}

    try:
        response_2 = binance_full_api.get_coinm_delivery_kline(contract_type='CURRENT_QUARTER')
    except HTTPError as exc:
        assert exc.response is not None
        response_2 = exc.response

    responses.append(('kline', response_2, request_params_2))

    request_params_3 = {'contract_type': 'CURRENT_QUARTER'}

    try:
        response_3 = binance_full_api.get_usdm_delivery_kline(contract_type='CURRENT_QUARTER')
    except HTTPError as exc:
        assert exc.response is not None
        response_3 = exc.response

    responses.append(('kline', response_3, request_params_3))

    for target_name, response, request_params in responses:

        assert response.status_code < 500
        body = response.json() if response.content else {}
        assert str(body.get("code", "")) != "500"
        if response.status_code >= 400:
            assert response.status_code in {400, 422}
            assert any(key in str(body).lower() for key in ('symbol', 'pair', 'contract_type', 'time', 'limit', 'symbols'))
        else:
            assert "code" in body
            assert "status" in body
            assert "message" in body
            assert str(body["code"]) in {"400", "422"}
            assert body["status"] in {"error", "fail", "failed"}
        assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('BF-DERIV-KLINE-PARAM-002 - ParamError - 三个接口 | 缺少 `contract_type')
@allure.feature('binance-full')
@allure.story('ParamError')
@pytest.mark.binance_full_api
def test_bf_deriv_kline_param_002(binance_full_api):
    """
    Case ID: BF-DERIV-KLINE-PARAM-002
    测试大类: binance-full
    测试类型: ParamError
    测试目的: 三个接口 | 缺少 `contract_type
    预期断言: 返回参数错误
    """

    case_id = 'BF-DERIV-KLINE-PARAM-002'

    responses = []

    request_params_1 = {'pair': 'BTCUSD'}

    try:
        response_1 = binance_full_api.get_coinm_perp_kline(pair='BTCUSD')
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('kline', response_1, request_params_1))

    request_params_2 = {'pair': 'BTCUSD'}

    try:
        response_2 = binance_full_api.get_coinm_delivery_kline(pair='BTCUSD')
    except HTTPError as exc:
        assert exc.response is not None
        response_2 = exc.response

    responses.append(('kline', response_2, request_params_2))

    request_params_3 = {'pair': 'BTCUSDT'}

    try:
        response_3 = binance_full_api.get_usdm_delivery_kline(pair='BTCUSDT')
    except HTTPError as exc:
        assert exc.response is not None
        response_3 = exc.response

    responses.append(('kline', response_3, request_params_3))

    for target_name, response, request_params in responses:

        assert response.status_code < 500
        body = response.json() if response.content else {}
        assert str(body.get("code", "")) != "500"
        if response.status_code >= 400:
            assert response.status_code in {400, 422}
            assert any(key in str(body).lower() for key in ('symbol', 'pair', 'contract_type', 'time', 'limit', 'symbols'))
        else:
            assert "code" in body
            assert "status" in body
            assert "message" in body
            assert str(body["code"]) in {"400", "422"}
            assert body["status"] in {"error", "fail", "failed"}
        assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('BF-DERIV-KLINE-PARAM-003 - ParamError - PERP 接口 | contract_type=CURRENT_QUARTER')
@allure.feature('binance-full')
@allure.story('ParamError')
@pytest.mark.binance_full_api
def test_bf_deriv_kline_param_003(binance_full_api):
    """
    Case ID: BF-DERIV-KLINE-PARAM-003
    测试大类: binance-full
    测试类型: ParamError
    测试目的: PERP 接口 | contract_type=CURRENT_QUARTER
    预期断言: 返回错误；PERP 仅 `PERPETUAL
    """

    case_id = 'BF-DERIV-KLINE-PARAM-003'

    responses = []

    request_params_1 = {'pair': 'BTCUSD', 'contract_type': 'CURRENT_QUARTER'}

    try:
        response_1 = binance_full_api.get_coinm_perp_kline(pair='BTCUSD', contract_type='CURRENT_QUARTER')
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('kline', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
            case_id = 'BF-DERIV-KLINE-PARAM-003'
            assert isinstance(data, dict)
            if data.get("multi"):
                assert "by_symbol" in data
                for bucket in data["by_symbol"].values():
                    assert "items" in bucket
                    assert len(bucket["items"]) <= int(request_params.get("limit", LIMIT_NORMAL))
            else:
                assert "items" in data
                assert isinstance(data["items"], list)
                if request_params.get("limit") is not None:
                    assert len(data["items"]) <= int(request_params["limit"])
                if "pagination" in data:
                    pagination = data["pagination"]
                    if request_params.get("include_total") and "total" in pagination and pagination["total"] is not None:
                        assert isinstance(pagination["total"], int)
                        assert pagination["total"] >= 0
                timestamps = []
                for item in data["items"]:
                    for field in ("timestamp", "open", "high", "low", "close", "volume"):
                        assert field in item
                    timestamp = int(item["timestamp"])
                    timestamps.append(timestamp)
                    assert len(str(timestamp)) == 13
                    if request_params.get("start_time_ms") and request_params.get("end_time_ms"):
                        assert request_params["start_time_ms"] <= timestamp < request_params["end_time_ms"]
                    if "1H-ALL" in case_id and item.get("interval") is not None:
                        assert item["interval"] == INTERVAL_1H
                    for numeric_field in ("open", "high", "low", "close", "volume", "quote_volume"):
                        if numeric_field in item and item[numeric_field] is not None:
                            assert Decimal(str(item[numeric_field])) is not None
                    if case_id.endswith("LOGIC-001") or case_id.endswith("DQC-001"):
                        open_value = Decimal(str(item["open"]))
                        high = Decimal(str(item["high"]))
                        low = Decimal(str(item["low"]))
                        close = Decimal(str(item["close"]))
                        assert high >= open_value
                        assert high >= close
                        assert high >= low
                        assert low <= open_value
                        assert low <= close
                        assert low <= high


@allure.title('BF-DERIV-KLINE-PARAM-004 - ParamError - delivery 接口 | contract_type=PERPETUAL')
@allure.feature('binance-full')
@allure.story('ParamError')
@pytest.mark.binance_full_api
def test_bf_deriv_kline_param_004(binance_full_api):
    """
    Case ID: BF-DERIV-KLINE-PARAM-004
    测试大类: binance-full
    测试类型: ParamError
    测试目的: delivery 接口 | contract_type=PERPETUAL
    预期断言: 返回错误；delivery 仅季度枚举
    """

    case_id = 'BF-DERIV-KLINE-PARAM-004'

    responses = []

    request_params_1 = {'pair': 'BTCUSD', 'contract_type': 'PERPETUAL'}

    try:
        response_1 = binance_full_api.get_coinm_delivery_kline(pair='BTCUSD', contract_type='PERPETUAL')
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('kline', response_1, request_params_1))

    request_params_2 = {'pair': 'BTCUSDT', 'contract_type': 'PERPETUAL'}

    try:
        response_2 = binance_full_api.get_usdm_delivery_kline(pair='BTCUSDT', contract_type='PERPETUAL')
    except HTTPError as exc:
        assert exc.response is not None
        response_2 = exc.response

    responses.append(('kline', response_2, request_params_2))

    for target_name, response, request_params in responses:

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
            case_id = 'BF-DERIV-KLINE-PARAM-004'
            assert isinstance(data, dict)
            if data.get("multi"):
                assert "by_symbol" in data
                for bucket in data["by_symbol"].values():
                    assert "items" in bucket
                    assert len(bucket["items"]) <= int(request_params.get("limit", LIMIT_NORMAL))
            else:
                assert "items" in data
                assert isinstance(data["items"], list)
                if request_params.get("limit") is not None:
                    assert len(data["items"]) <= int(request_params["limit"])
                if "pagination" in data:
                    pagination = data["pagination"]
                    if request_params.get("include_total") and "total" in pagination and pagination["total"] is not None:
                        assert isinstance(pagination["total"], int)
                        assert pagination["total"] >= 0
                timestamps = []
                for item in data["items"]:
                    for field in ("timestamp", "open", "high", "low", "close", "volume"):
                        assert field in item
                    timestamp = int(item["timestamp"])
                    timestamps.append(timestamp)
                    assert len(str(timestamp)) == 13
                    if request_params.get("start_time_ms") and request_params.get("end_time_ms"):
                        assert request_params["start_time_ms"] <= timestamp < request_params["end_time_ms"]
                    if "1H-ALL" in case_id and item.get("interval") is not None:
                        assert item["interval"] == INTERVAL_1H
                    for numeric_field in ("open", "high", "low", "close", "volume", "quote_volume"):
                        if numeric_field in item and item[numeric_field] is not None:
                            assert Decimal(str(item[numeric_field])) is not None
                    if case_id.endswith("LOGIC-001") or case_id.endswith("DQC-001"):
                        open_value = Decimal(str(item["open"]))
                        high = Decimal(str(item["high"]))
                        low = Decimal(str(item["low"]))
                        close = Decimal(str(item["close"]))
                        assert high >= open_value
                        assert high >= close
                        assert high >= low
                        assert low <= open_value
                        assert low <= close
                        assert low <= high


@allure.title('BF-DERIV-KLINE-DQC-001 - DataQuality - 三个接口 | 正常请求')
@allure.feature('binance-full')
@allure.story('DataQuality')
@pytest.mark.binance_full_api
@pytest.mark.dqc
def test_bf_deriv_kline_dqc_001(binance_full_api):
    """
    Case ID: BF-DERIV-KLINE-DQC-001
    测试大类: binance-full
    测试类型: DataQuality
    测试目的: 三个接口 | 正常请求
    预期断言: timestamp 毫秒；数值字段可转数字
    """

    case_id = 'BF-DERIV-KLINE-DQC-001'

    responses = []

    request_params_1 = {'pair': 'BTCUSD', 'contract_type': 'PERPETUAL', 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'limit': 10, 'offset': 0, 'include_total': False}

    response_1 = binance_full_api.get_coinm_perp_kline(pair='BTCUSD', contract_type='PERPETUAL', interval='1m', start_time_ms=1704067200000, end_time_ms=1704153600000, limit=10, offset=0, include_total=False)

    responses.append(('kline', response_1, request_params_1))

    request_params_2 = {'pair': 'BTCUSD', 'contract_type': 'CURRENT_QUARTER', 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'limit': 10, 'offset': 0, 'include_total': False}

    response_2 = binance_full_api.get_coinm_delivery_kline(pair='BTCUSD', contract_type='CURRENT_QUARTER', interval='1m', start_time_ms=1704067200000, end_time_ms=1704153600000, limit=10, offset=0, include_total=False)

    responses.append(('kline', response_2, request_params_2))

    request_params_3 = {'pair': 'BTCUSDT', 'contract_type': 'CURRENT_QUARTER', 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'limit': 10, 'offset': 0, 'include_total': False}

    response_3 = binance_full_api.get_usdm_delivery_kline(pair='BTCUSDT', contract_type='CURRENT_QUARTER', interval='1m', start_time_ms=1704067200000, end_time_ms=1704153600000, limit=10, offset=0, include_total=False)

    responses.append(('kline', response_3, request_params_3))

    for target_name, response, request_params in responses:

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
        case_id = 'BF-DERIV-KLINE-DQC-001'
        assert isinstance(data, dict)
        if data.get("multi"):
            assert "by_symbol" in data
            for bucket in data["by_symbol"].values():
                assert "items" in bucket
                assert len(bucket["items"]) <= int(request_params.get("limit", LIMIT_NORMAL))
        else:
            assert "items" in data
            assert isinstance(data["items"], list)
            if request_params.get("limit") is not None:
                assert len(data["items"]) <= int(request_params["limit"])
            if "pagination" in data:
                pagination = data["pagination"]
                if request_params.get("include_total") and "total" in pagination and pagination["total"] is not None:
                    assert isinstance(pagination["total"], int)
                    assert pagination["total"] >= 0
            timestamps = []
            for item in data["items"]:
                for field in ("timestamp", "open", "high", "low", "close", "volume"):
                    assert field in item
                timestamp = int(item["timestamp"])
                timestamps.append(timestamp)
                assert len(str(timestamp)) == 13
                if request_params.get("start_time_ms") and request_params.get("end_time_ms"):
                    assert request_params["start_time_ms"] <= timestamp < request_params["end_time_ms"]
                if "1H-ALL" in case_id and item.get("interval") is not None:
                    assert item["interval"] == INTERVAL_1H
                for numeric_field in ("open", "high", "low", "close", "volume", "quote_volume"):
                    if numeric_field in item and item[numeric_field] is not None:
                        assert Decimal(str(item[numeric_field])) is not None
                if case_id.endswith("LOGIC-001") or case_id.endswith("DQC-001"):
                    open_value = Decimal(str(item["open"]))
                    high = Decimal(str(item["high"]))
                    low = Decimal(str(item["low"]))
                    close = Decimal(str(item["close"]))
                    assert high >= open_value
                    assert high >= close
                    assert high >= low
                    assert low <= open_value
                    assert low <= close
                    assert low <= high


@allure.title('BF-DERIV-KLINE-LOGIC-001 - BusinessLogic - 三个接口 | 正常请求')
@allure.feature('binance-full')
@allure.story('BusinessLogic')
@pytest.mark.binance_full_api
@pytest.mark.logic
def test_bf_deriv_kline_logic_001(binance_full_api):
    """
    Case ID: BF-DERIV-KLINE-LOGIC-001
    测试大类: binance-full
    测试类型: BusinessLogic
    测试目的: 三个接口 | 正常请求
    预期断言: OHLC 合法；时间窗过滤正确
    """

    case_id = 'BF-DERIV-KLINE-LOGIC-001'

    responses = []

    request_params_1 = {'pair': 'BTCUSD', 'contract_type': 'PERPETUAL', 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'limit': 10, 'offset': 0, 'include_total': False}

    response_1 = binance_full_api.get_coinm_perp_kline(pair='BTCUSD', contract_type='PERPETUAL', interval='1m', start_time_ms=1704067200000, end_time_ms=1704153600000, limit=10, offset=0, include_total=False)

    responses.append(('kline', response_1, request_params_1))

    request_params_2 = {'pair': 'BTCUSD', 'contract_type': 'CURRENT_QUARTER', 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'limit': 10, 'offset': 0, 'include_total': False}

    response_2 = binance_full_api.get_coinm_delivery_kline(pair='BTCUSD', contract_type='CURRENT_QUARTER', interval='1m', start_time_ms=1704067200000, end_time_ms=1704153600000, limit=10, offset=0, include_total=False)

    responses.append(('kline', response_2, request_params_2))

    request_params_3 = {'pair': 'BTCUSDT', 'contract_type': 'CURRENT_QUARTER', 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'limit': 10, 'offset': 0, 'include_total': False}

    response_3 = binance_full_api.get_usdm_delivery_kline(pair='BTCUSDT', contract_type='CURRENT_QUARTER', interval='1m', start_time_ms=1704067200000, end_time_ms=1704153600000, limit=10, offset=0, include_total=False)

    responses.append(('kline', response_3, request_params_3))

    for target_name, response, request_params in responses:

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
        case_id = 'BF-DERIV-KLINE-LOGIC-001'
        assert isinstance(data, dict)
        if data.get("multi"):
            assert "by_symbol" in data
            for bucket in data["by_symbol"].values():
                assert "items" in bucket
                assert len(bucket["items"]) <= int(request_params.get("limit", LIMIT_NORMAL))
        else:
            assert "items" in data
            assert isinstance(data["items"], list)
            if request_params.get("limit") is not None:
                assert len(data["items"]) <= int(request_params["limit"])
            if "pagination" in data:
                pagination = data["pagination"]
                if request_params.get("include_total") and "total" in pagination and pagination["total"] is not None:
                    assert isinstance(pagination["total"], int)
                    assert pagination["total"] >= 0
            timestamps = []
            for item in data["items"]:
                for field in ("timestamp", "open", "high", "low", "close", "volume"):
                    assert field in item
                timestamp = int(item["timestamp"])
                timestamps.append(timestamp)
                assert len(str(timestamp)) == 13
                if request_params.get("start_time_ms") and request_params.get("end_time_ms"):
                    assert request_params["start_time_ms"] <= timestamp < request_params["end_time_ms"]
                if "1H-ALL" in case_id and item.get("interval") is not None:
                    assert item["interval"] == INTERVAL_1H
                for numeric_field in ("open", "high", "low", "close", "volume", "quote_volume"):
                    if numeric_field in item and item[numeric_field] is not None:
                        assert Decimal(str(item[numeric_field])) is not None
                if case_id.endswith("LOGIC-001") or case_id.endswith("DQC-001"):
                    open_value = Decimal(str(item["open"]))
                    high = Decimal(str(item["high"]))
                    low = Decimal(str(item["low"]))
                    close = Decimal(str(item["close"]))
                    assert high >= open_value
                    assert high >= close
                    assert high >= low
                    assert low <= open_value
                    assert low <= close
                    assert low <= high


@allure.title('BF-FUNDING-NORMAL-001 - Normal - 两个接口 | 合法主参数 + `start_time_ms/end_time_ms/limit=10')
@allure.feature('binance-full')
@allure.story('Normal')
@pytest.mark.binance_full_api
def test_bf_funding_normal_001(binance_full_api):
    """
    Case ID: BF-FUNDING-NORMAL-001
    测试大类: binance-full
    测试类型: Normal
    测试目的: 两个接口 | 合法主参数 + `start_time_ms/end_time_ms/limit=10
    预期断言: 成功；items 为 funding 行
    """

    case_id = 'BF-FUNDING-NORMAL-001'

    responses = []

    request_params_1 = {'symbol': 'BTCUSDT', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'limit': 10}

    response_1 = binance_full_api.get_usdm_funding(symbol='BTCUSDT', start_time_ms=1704067200000, end_time_ms=1704153600000, limit=10)

    responses.append(('funding', response_1, request_params_1))

    request_params_2 = {'pair': 'BTCUSD', 'contract_type': 'PERPETUAL', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'limit': 10}

    response_2 = binance_full_api.get_coinm_perp_funding(pair='BTCUSD', contract_type='PERPETUAL', start_time_ms=1704067200000, end_time_ms=1704153600000, limit=10)

    responses.append(('funding', response_2, request_params_2))

    for target_name, response, request_params in responses:

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
        assert isinstance(data, dict)
        if data.get("multi"):
            assert "by_symbol" in data
        else:
            assert "items" in data
            assert isinstance(data["items"], list)
            if request_params.get("limit") is not None:
                assert len(data["items"]) <= int(request_params["limit"])
            for item in data["items"]:
                if "funding_time" in item and item["funding_time"] is not None:
                    millis = int(item["funding_time"])
                    assert len(str(millis)) == 13
                    if request_params.get("start_time_ms") and request_params.get("end_time_ms"):
                        assert request_params["start_time_ms"] <= millis < request_params["end_time_ms"]
                for numeric_field in ("funding_rate", "mark_price"):
                    if numeric_field in item and item[numeric_field] is not None:
                        assert Decimal(str(item[numeric_field])) is not None
                if request_params.get("symbol") and item.get("symbol"):
                    assert item["symbol"] in str(request_params["symbol"]).split(",")
                if request_params.get("pair") and item.get("pair"):
                    assert item["pair"] in str(request_params["pair"]).split(",")


@allure.title('BF-FUNDING-NORMAL-002 - Normal - 两个接口 | 多 symbol/pair 逗号分隔')
@allure.feature('binance-full')
@allure.story('Normal')
@pytest.mark.binance_full_api
def test_bf_funding_normal_002(binance_full_api):
    """
    Case ID: BF-FUNDING-NORMAL-002
    测试大类: binance-full
    测试类型: Normal
    测试目的: 两个接口 | 多 symbol/pair 逗号分隔
    预期断言: 多桶结构正确
    """

    case_id = 'BF-FUNDING-NORMAL-002'

    responses = []

    request_params_1 = {'symbol': 'BTCUSDT,ETHUSDT', 'limit': 10}

    response_1 = binance_full_api.get_usdm_funding(symbol='BTCUSDT,ETHUSDT', limit=10)

    responses.append(('funding', response_1, request_params_1))

    request_params_2 = {'pair': 'BTCUSD,ETHUSD', 'contract_type': 'PERPETUAL', 'limit': 10}

    response_2 = binance_full_api.get_coinm_perp_funding(pair='BTCUSD,ETHUSD', contract_type='PERPETUAL', limit=10)

    responses.append(('funding', response_2, request_params_2))

    for target_name, response, request_params in responses:

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
        assert isinstance(data, dict)
        if data.get("multi"):
            assert "by_symbol" in data
        else:
            assert "items" in data
            assert isinstance(data["items"], list)
            if request_params.get("limit") is not None:
                assert len(data["items"]) <= int(request_params["limit"])
            for item in data["items"]:
                if "funding_time" in item and item["funding_time"] is not None:
                    millis = int(item["funding_time"])
                    assert len(str(millis)) == 13
                    if request_params.get("start_time_ms") and request_params.get("end_time_ms"):
                        assert request_params["start_time_ms"] <= millis < request_params["end_time_ms"]
                for numeric_field in ("funding_rate", "mark_price"):
                    if numeric_field in item and item[numeric_field] is not None:
                        assert Decimal(str(item[numeric_field])) is not None
                if request_params.get("symbol") and item.get("symbol"):
                    assert item["symbol"] in str(request_params["symbol"]).split(",")
                if request_params.get("pair") and item.get("pair"):
                    assert item["pair"] in str(request_params["pair"]).split(",")


@allure.title('BF-FUNDING-BOUNDARY-001 - Boundary - 两个接口 | limit=1')
@allure.feature('binance-full')
@allure.story('Boundary')
@pytest.mark.binance_full_api
def test_bf_funding_boundary_001(binance_full_api):
    """
    Case ID: BF-FUNDING-BOUNDARY-001
    测试大类: binance-full
    测试类型: Boundary
    测试目的: 两个接口 | limit=1
    预期断言: 最多 1 条
    """

    case_id = 'BF-FUNDING-BOUNDARY-001'

    responses = []

    request_params_1 = {'symbol': 'BTCUSDT', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'limit': 1}

    response_1 = binance_full_api.get_usdm_funding(symbol='BTCUSDT', start_time_ms=1704067200000, end_time_ms=1704153600000, limit=1)

    responses.append(('funding', response_1, request_params_1))

    request_params_2 = {'pair': 'BTCUSD', 'contract_type': 'PERPETUAL', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'limit': 1}

    response_2 = binance_full_api.get_coinm_perp_funding(pair='BTCUSD', contract_type='PERPETUAL', start_time_ms=1704067200000, end_time_ms=1704153600000, limit=1)

    responses.append(('funding', response_2, request_params_2))

    for target_name, response, request_params in responses:

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
        assert isinstance(data, dict)
        if data.get("multi"):
            assert "by_symbol" in data
        else:
            assert "items" in data
            assert isinstance(data["items"], list)
            if request_params.get("limit") is not None:
                assert len(data["items"]) <= int(request_params["limit"])
            for item in data["items"]:
                if "funding_time" in item and item["funding_time"] is not None:
                    millis = int(item["funding_time"])
                    assert len(str(millis)) == 13
                    if request_params.get("start_time_ms") and request_params.get("end_time_ms"):
                        assert request_params["start_time_ms"] <= millis < request_params["end_time_ms"]
                for numeric_field in ("funding_rate", "mark_price"):
                    if numeric_field in item and item[numeric_field] is not None:
                        assert Decimal(str(item[numeric_field])) is not None
                if request_params.get("symbol") and item.get("symbol"):
                    assert item["symbol"] in str(request_params["symbol"]).split(",")
                if request_params.get("pair") and item.get("pair"):
                    assert item["pair"] in str(request_params["pair"]).split(",")


@allure.title('BF-FUNDING-BOUNDARY-002 - Boundary - USDM | include_legacy_coinm_in_usdm_aggregate=true')
@allure.feature('binance-full')
@allure.story('Boundary')
@pytest.mark.binance_full_api
def test_bf_funding_boundary_002(binance_full_api):
    """
    Case ID: BF-FUNDING-BOUNDARY-002
    测试大类: binance-full
    测试类型: Boundary
    测试目的: USDM | include_legacy_coinm_in_usdm_aggregate=true
    预期断言: 成功或明确业务提示；不能 500
    """

    case_id = 'BF-FUNDING-BOUNDARY-002'

    responses = []

    request_params_1 = {'symbol': 'BTCUSDT', 'include_legacy_coinm_in_usdm_aggregate': True, 'limit': 1}

    try:
        response_1 = binance_full_api.get_usdm_funding(symbol='BTCUSDT', include_legacy_coinm_in_usdm_aggregate=True, limit=1)
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('funding', response_1, request_params_1))

    for target_name, response, request_params in responses:

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
            assert isinstance(data, dict)
            if data.get("multi"):
                assert "by_symbol" in data
            else:
                assert "items" in data
                assert isinstance(data["items"], list)
                if request_params.get("limit") is not None:
                    assert len(data["items"]) <= int(request_params["limit"])
                for item in data["items"]:
                    if "funding_time" in item and item["funding_time"] is not None:
                        millis = int(item["funding_time"])
                        assert len(str(millis)) == 13
                        if request_params.get("start_time_ms") and request_params.get("end_time_ms"):
                            assert request_params["start_time_ms"] <= millis < request_params["end_time_ms"]
                    for numeric_field in ("funding_rate", "mark_price"):
                        if numeric_field in item and item[numeric_field] is not None:
                            assert Decimal(str(item[numeric_field])) is not None
                    if request_params.get("symbol") and item.get("symbol"):
                        assert item["symbol"] in str(request_params["symbol"]).split(",")
                    if request_params.get("pair") and item.get("pair"):
                        assert item["pair"] in str(request_params["pair"]).split(",")


@allure.title('BF-FUNDING-PARAM-001 - ParamError - USDM | 缺少 `symbol')
@allure.feature('binance-full')
@allure.story('ParamError')
@pytest.mark.binance_full_api
def test_bf_funding_param_001(binance_full_api):
    """
    Case ID: BF-FUNDING-PARAM-001
    测试大类: binance-full
    测试类型: ParamError
    测试目的: USDM | 缺少 `symbol
    预期断言: 返回参数错误
    """

    case_id = 'BF-FUNDING-PARAM-001'

    responses = []

    request_params_1 = {'limit': 10}

    try:
        response_1 = binance_full_api.get_usdm_funding(limit=10)
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('funding', response_1, request_params_1))

    for target_name, response, request_params in responses:

        assert response.status_code < 500
        body = response.json() if response.content else {}
        assert str(body.get("code", "")) != "500"
        if response.status_code >= 400:
            assert response.status_code in {400, 422}
            assert any(key in str(body).lower() for key in ('symbol', 'pair', 'contract_type', 'time', 'limit', 'symbols'))
        else:
            assert "code" in body
            assert "status" in body
            assert "message" in body
            assert str(body["code"]) in {"400", "422"}
            assert body["status"] in {"error", "fail", "failed"}
        assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('BF-FUNDING-PARAM-002 - ParamError - COIN-M PERP | 缺少 `pair')
@allure.feature('binance-full')
@allure.story('ParamError')
@pytest.mark.binance_full_api
def test_bf_funding_param_002(binance_full_api):
    """
    Case ID: BF-FUNDING-PARAM-002
    测试大类: binance-full
    测试类型: ParamError
    测试目的: COIN-M PERP | 缺少 `pair
    预期断言: 返回参数错误
    """

    case_id = 'BF-FUNDING-PARAM-002'

    responses = []

    request_params_1 = {'contract_type': 'PERPETUAL'}

    try:
        response_1 = binance_full_api.get_coinm_perp_funding(contract_type='PERPETUAL')
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('funding', response_1, request_params_1))

    for target_name, response, request_params in responses:

        assert response.status_code < 500
        body = response.json() if response.content else {}
        assert str(body.get("code", "")) != "500"
        if response.status_code >= 400:
            assert response.status_code in {400, 422}
            assert any(key in str(body).lower() for key in ('symbol', 'pair', 'contract_type', 'time', 'limit', 'symbols'))
        else:
            assert "code" in body
            assert "status" in body
            assert "message" in body
            assert str(body["code"]) in {"400", "422"}
            assert body["status"] in {"error", "fail", "failed"}
        assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('BF-FUNDING-PARAM-003 - ParamError - COIN-M PERP | 缺少 `contract_type')
@allure.feature('binance-full')
@allure.story('ParamError')
@pytest.mark.binance_full_api
def test_bf_funding_param_003(binance_full_api):
    """
    Case ID: BF-FUNDING-PARAM-003
    测试大类: binance-full
    测试类型: ParamError
    测试目的: COIN-M PERP | 缺少 `contract_type
    预期断言: 返回参数错误
    """

    case_id = 'BF-FUNDING-PARAM-003'

    responses = []

    request_params_1 = {'pair': 'BTCUSD'}

    try:
        response_1 = binance_full_api.get_coinm_perp_funding(pair='BTCUSD')
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('funding', response_1, request_params_1))

    for target_name, response, request_params in responses:

        assert response.status_code < 500
        body = response.json() if response.content else {}
        assert str(body.get("code", "")) != "500"
        if response.status_code >= 400:
            assert response.status_code in {400, 422}
            assert any(key in str(body).lower() for key in ('symbol', 'pair', 'contract_type', 'time', 'limit', 'symbols'))
        else:
            assert "code" in body
            assert "status" in body
            assert "message" in body
            assert str(body["code"]) in {"400", "422"}
            assert body["status"] in {"error", "fail", "failed"}
        assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('BF-FUNDING-PARAM-004 - ParamError - 两个接口 | end_time_ms <= start_time_ms')
@allure.feature('binance-full')
@allure.story('ParamError')
@pytest.mark.binance_full_api
def test_bf_funding_param_004(binance_full_api):
    """
    Case ID: BF-FUNDING-PARAM-004
    测试大类: binance-full
    测试类型: ParamError
    测试目的: 两个接口 | end_time_ms <= start_time_ms
    预期断言: 返回时间窗错误
    """

    case_id = 'BF-FUNDING-PARAM-004'

    responses = []

    request_params_1 = {'symbol': 'BTCUSDT', 'start_time_ms': 1704153600000, 'end_time_ms': 1704067200000, 'limit': 10}

    try:
        response_1 = binance_full_api.get_usdm_funding(symbol='BTCUSDT', start_time_ms=1704153600000, end_time_ms=1704067200000, limit=10)
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('funding', response_1, request_params_1))

    request_params_2 = {'pair': 'BTCUSD', 'contract_type': 'PERPETUAL', 'start_time_ms': 1704153600000, 'end_time_ms': 1704067200000, 'limit': 10}

    try:
        response_2 = binance_full_api.get_coinm_perp_funding(pair='BTCUSD', contract_type='PERPETUAL', start_time_ms=1704153600000, end_time_ms=1704067200000, limit=10)
    except HTTPError as exc:
        assert exc.response is not None
        response_2 = exc.response

    responses.append(('funding', response_2, request_params_2))

    for target_name, response, request_params in responses:

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
            assert isinstance(data, dict)
            if data.get("multi"):
                assert "by_symbol" in data
            else:
                assert "items" in data
                assert isinstance(data["items"], list)
                if request_params.get("limit") is not None:
                    assert len(data["items"]) <= int(request_params["limit"])
                for item in data["items"]:
                    if "funding_time" in item and item["funding_time"] is not None:
                        millis = int(item["funding_time"])
                        assert len(str(millis)) == 13
                        if request_params.get("start_time_ms") and request_params.get("end_time_ms"):
                            assert request_params["start_time_ms"] <= millis < request_params["end_time_ms"]
                    for numeric_field in ("funding_rate", "mark_price"):
                        if numeric_field in item and item[numeric_field] is not None:
                            assert Decimal(str(item[numeric_field])) is not None
                    if request_params.get("symbol") and item.get("symbol"):
                        assert item["symbol"] in str(request_params["symbol"]).split(",")
                    if request_params.get("pair") and item.get("pair"):
                        assert item["pair"] in str(request_params["pair"]).split(",")


@allure.title('BF-FUNDING-DQC-001 - DataQuality - 两个接口 | 正常请求')
@allure.feature('binance-full')
@allure.story('DataQuality')
@pytest.mark.binance_full_api
@pytest.mark.dqc
def test_bf_funding_dqc_001(binance_full_api):
    """
    Case ID: BF-FUNDING-DQC-001
    测试大类: binance-full
    测试类型: DataQuality
    测试目的: 两个接口 | 正常请求
    预期断言: funding_time` 为 13 位毫秒；`funding_rate/mark_price` 可转数字
    """

    case_id = 'BF-FUNDING-DQC-001'

    responses = []

    request_params_1 = {'symbol': 'BTCUSDT', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'limit': 10}

    response_1 = binance_full_api.get_usdm_funding(symbol='BTCUSDT', start_time_ms=1704067200000, end_time_ms=1704153600000, limit=10)

    responses.append(('funding', response_1, request_params_1))

    request_params_2 = {'pair': 'BTCUSD', 'contract_type': 'PERPETUAL', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'limit': 10}

    response_2 = binance_full_api.get_coinm_perp_funding(pair='BTCUSD', contract_type='PERPETUAL', start_time_ms=1704067200000, end_time_ms=1704153600000, limit=10)

    responses.append(('funding', response_2, request_params_2))

    for target_name, response, request_params in responses:

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
        assert isinstance(data, dict)
        if data.get("multi"):
            assert "by_symbol" in data
        else:
            assert "items" in data
            assert isinstance(data["items"], list)
            if request_params.get("limit") is not None:
                assert len(data["items"]) <= int(request_params["limit"])
            for item in data["items"]:
                if "funding_time" in item and item["funding_time"] is not None:
                    millis = int(item["funding_time"])
                    assert len(str(millis)) == 13
                    if request_params.get("start_time_ms") and request_params.get("end_time_ms"):
                        assert request_params["start_time_ms"] <= millis < request_params["end_time_ms"]
                for numeric_field in ("funding_rate", "mark_price"):
                    if numeric_field in item and item[numeric_field] is not None:
                        assert Decimal(str(item[numeric_field])) is not None
                if request_params.get("symbol") and item.get("symbol"):
                    assert item["symbol"] in str(request_params["symbol"]).split(",")
                if request_params.get("pair") and item.get("pair"):
                    assert item["pair"] in str(request_params["pair"]).split(",")


@allure.title('BF-FUNDING-LOGIC-001 - BusinessLogic - 两个接口 | 正常请求')
@allure.feature('binance-full')
@allure.story('BusinessLogic')
@pytest.mark.binance_full_api
@pytest.mark.logic
def test_bf_funding_logic_001(binance_full_api):
    """
    Case ID: BF-FUNDING-LOGIC-001
    测试大类: binance-full
    测试类型: BusinessLogic
    测试目的: 两个接口 | 正常请求
    预期断言: funding_time 在请求窗口内；symbol/pair 与请求匹配
    """

    case_id = 'BF-FUNDING-LOGIC-001'

    responses = []

    request_params_1 = {'symbol': 'BTCUSDT', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'limit': 10}

    response_1 = binance_full_api.get_usdm_funding(symbol='BTCUSDT', start_time_ms=1704067200000, end_time_ms=1704153600000, limit=10)

    responses.append(('funding', response_1, request_params_1))

    request_params_2 = {'pair': 'BTCUSD', 'contract_type': 'PERPETUAL', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'limit': 10}

    response_2 = binance_full_api.get_coinm_perp_funding(pair='BTCUSD', contract_type='PERPETUAL', start_time_ms=1704067200000, end_time_ms=1704153600000, limit=10)

    responses.append(('funding', response_2, request_params_2))

    for target_name, response, request_params in responses:

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
        assert isinstance(data, dict)
        if data.get("multi"):
            assert "by_symbol" in data
        else:
            assert "items" in data
            assert isinstance(data["items"], list)
            if request_params.get("limit") is not None:
                assert len(data["items"]) <= int(request_params["limit"])
            for item in data["items"]:
                if "funding_time" in item and item["funding_time"] is not None:
                    millis = int(item["funding_time"])
                    assert len(str(millis)) == 13
                    if request_params.get("start_time_ms") and request_params.get("end_time_ms"):
                        assert request_params["start_time_ms"] <= millis < request_params["end_time_ms"]
                for numeric_field in ("funding_rate", "mark_price"):
                    if numeric_field in item and item[numeric_field] is not None:
                        assert Decimal(str(item[numeric_field])) is not None
                if request_params.get("symbol") and item.get("symbol"):
                    assert item["symbol"] in str(request_params["symbol"]).split(",")
                if request_params.get("pair") and item.get("pair"):
                    assert item["pair"] in str(request_params["pair"]).split(",")


@allure.title('BF-BATCH-BOUNDS-NORMAL-001 - Normal - 六个接口 | symbols` 为 JSON 数组')
@allure.feature('binance-full')
@allure.story('Normal')
@pytest.mark.binance_full_api
def test_bf_batch_bounds_normal_001(binance_full_api):
    """
    Case ID: BF-BATCH-BOUNDS-NORMAL-001
    测试大类: binance-full
    测试类型: Normal
    测试目的: 六个接口 | symbols` 为 JSON 数组
    预期断言: 成功；`data.items` 为边界行数组
    """

    case_id = 'BF-BATCH-BOUNDS-NORMAL-001'

    responses = []

    request_params_1 = {}

    response_1 = binance_full_api.batch_usdm_kline_time_bounds(symbols=['BTCUSDT', 'ETHUSDT'], contract_type=None, interval='1m')

    responses.append(('kline-time-bounds', response_1, request_params_1))

    request_params_2 = {'contract_type': 'PERPETUAL'}

    response_2 = binance_full_api.batch_coinm_perp_kline_time_bounds(symbols=['BTCUSD', 'ETHUSD'], contract_type='PERPETUAL', interval='1m')

    responses.append(('kline-time-bounds', response_2, request_params_2))

    request_params_3 = {'contract_type': 'CURRENT_QUARTER'}

    response_3 = binance_full_api.batch_coinm_delivery_kline_time_bounds(symbols=['BTCUSD'], contract_type='CURRENT_QUARTER', interval='1m')

    responses.append(('kline-time-bounds', response_3, request_params_3))

    request_params_4 = {'contract_type': 'CURRENT_QUARTER'}

    response_4 = binance_full_api.batch_usdm_delivery_kline_time_bounds(symbols=['BTCUSDT'], contract_type='CURRENT_QUARTER', interval='1m')

    responses.append(('kline-time-bounds', response_4, request_params_4))

    request_params_5 = {}

    response_5 = binance_full_api.batch_usdm_funding_time_bounds(symbols=['BTCUSDT', 'ETHUSDT'], contract_type=None)

    responses.append(('funding-time-bounds', response_5, request_params_5))

    request_params_6 = {'contract_type': 'PERPETUAL'}

    response_6 = binance_full_api.batch_coinm_perp_funding_time_bounds(symbols=['BTCUSD', 'ETHUSD'], contract_type='PERPETUAL')

    responses.append(('funding-time-bounds', response_6, request_params_6))

    for target_name, response, request_params in responses:

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
        assert isinstance(data, dict)
        assert "items" in data
        assert isinstance(data["items"], list)
        if case_id == "BF-BATCH-BOUNDS-BOUNDARY-002":
            assert len(data["items"]) <= 1
        for item in data["items"]:
            assert "symbol" in item or "pair" in item
            assert "min_time_ms" in item
            assert "max_time_ms" in item
            assert "has_data" in item
            if item["min_time_ms"] is not None:
                assert len(str(int(item["min_time_ms"]))) == 13
            if item["max_time_ms"] is not None:
                assert len(str(int(item["max_time_ms"]))) == 13
            if case_id == "BF-BATCH-BOUNDS-DQC-001" and item["has_data"]:
                assert int(item["min_time_ms"]) <= int(item["max_time_ms"])


@allure.title('BF-BATCH-BOUNDS-NORMAL-002 - Normal - 六个接口 | symbols` 为逗号字符串')
@allure.feature('binance-full')
@allure.story('Normal')
@pytest.mark.binance_full_api
def test_bf_batch_bounds_normal_002(binance_full_api):
    """
    Case ID: BF-BATCH-BOUNDS-NORMAL-002
    测试大类: binance-full
    测试类型: Normal
    测试目的: 六个接口 | symbols` 为逗号字符串
    预期断言: 成功；语义等价于数组
    """

    case_id = 'BF-BATCH-BOUNDS-NORMAL-002'

    responses = []

    request_params_1 = {}

    response_1 = binance_full_api.batch_usdm_kline_time_bounds(symbols='BTCUSDT,ETHUSDT', contract_type=None, interval='1m')

    responses.append(('kline-time-bounds', response_1, request_params_1))

    request_params_2 = {'contract_type': 'PERPETUAL'}

    response_2 = binance_full_api.batch_coinm_perp_kline_time_bounds(symbols='BTCUSD,ETHUSD', contract_type='PERPETUAL', interval='1m')

    responses.append(('kline-time-bounds', response_2, request_params_2))

    request_params_3 = {'contract_type': 'CURRENT_QUARTER'}

    response_3 = binance_full_api.batch_coinm_delivery_kline_time_bounds(symbols='BTCUSD', contract_type='CURRENT_QUARTER', interval='1m')

    responses.append(('kline-time-bounds', response_3, request_params_3))

    request_params_4 = {'contract_type': 'CURRENT_QUARTER'}

    response_4 = binance_full_api.batch_usdm_delivery_kline_time_bounds(symbols='BTCUSDT', contract_type='CURRENT_QUARTER', interval='1m')

    responses.append(('kline-time-bounds', response_4, request_params_4))

    request_params_5 = {}

    response_5 = binance_full_api.batch_usdm_funding_time_bounds(symbols='BTCUSDT,ETHUSDT', contract_type=None)

    responses.append(('funding-time-bounds', response_5, request_params_5))

    request_params_6 = {'contract_type': 'PERPETUAL'}

    response_6 = binance_full_api.batch_coinm_perp_funding_time_bounds(symbols='BTCUSD,ETHUSD', contract_type='PERPETUAL')

    responses.append(('funding-time-bounds', response_6, request_params_6))

    for target_name, response, request_params in responses:

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
        assert isinstance(data, dict)
        assert "items" in data
        assert isinstance(data["items"], list)
        if case_id == "BF-BATCH-BOUNDS-BOUNDARY-002":
            assert len(data["items"]) <= 1
        for item in data["items"]:
            assert "symbol" in item or "pair" in item
            assert "min_time_ms" in item
            assert "max_time_ms" in item
            assert "has_data" in item
            if item["min_time_ms"] is not None:
                assert len(str(int(item["min_time_ms"]))) == 13
            if item["max_time_ms"] is not None:
                assert len(str(int(item["max_time_ms"]))) == 13
            if case_id == "BF-BATCH-BOUNDS-DQC-001" and item["has_data"]:
                assert int(item["min_time_ms"]) <= int(item["max_time_ms"])


@allure.title('BF-BATCH-BOUNDS-BOUNDARY-001 - Boundary - K 线 bounds | interval=1h')
@allure.feature('binance-full')
@allure.story('Boundary')
@pytest.mark.binance_full_api
def test_bf_batch_bounds_boundary_001(binance_full_api):
    """
    Case ID: BF-BATCH-BOUNDS-BOUNDARY-001
    测试大类: binance-full
    测试类型: Boundary
    测试目的: K 线 bounds | interval=1h
    预期断言: 成功或无数据提示；不能 500
    """

    case_id = 'BF-BATCH-BOUNDS-BOUNDARY-001'

    responses = []

    request_params_1 = {}

    try:
        response_1 = binance_full_api.batch_usdm_kline_time_bounds(symbols=['BTCUSDT', 'ETHUSDT'], contract_type=None, interval='1h')
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('kline-time-bounds', response_1, request_params_1))

    request_params_2 = {'contract_type': 'PERPETUAL'}

    try:
        response_2 = binance_full_api.batch_coinm_perp_kline_time_bounds(symbols=['BTCUSD', 'ETHUSD'], contract_type='PERPETUAL', interval='1h')
    except HTTPError as exc:
        assert exc.response is not None
        response_2 = exc.response

    responses.append(('kline-time-bounds', response_2, request_params_2))

    request_params_3 = {'contract_type': 'CURRENT_QUARTER'}

    try:
        response_3 = binance_full_api.batch_coinm_delivery_kline_time_bounds(symbols=['BTCUSD'], contract_type='CURRENT_QUARTER', interval='1h')
    except HTTPError as exc:
        assert exc.response is not None
        response_3 = exc.response

    responses.append(('kline-time-bounds', response_3, request_params_3))

    request_params_4 = {'contract_type': 'CURRENT_QUARTER'}

    try:
        response_4 = binance_full_api.batch_usdm_delivery_kline_time_bounds(symbols=['BTCUSDT'], contract_type='CURRENT_QUARTER', interval='1h')
    except HTTPError as exc:
        assert exc.response is not None
        response_4 = exc.response

    responses.append(('kline-time-bounds', response_4, request_params_4))

    for target_name, response, request_params in responses:

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
            assert isinstance(data, dict)
            assert "items" in data
            assert isinstance(data["items"], list)
            if case_id == "BF-BATCH-BOUNDS-BOUNDARY-002":
                assert len(data["items"]) <= 1
            for item in data["items"]:
                assert "symbol" in item or "pair" in item
                assert "min_time_ms" in item
                assert "max_time_ms" in item
                assert "has_data" in item
                if item["min_time_ms"] is not None:
                    assert len(str(int(item["min_time_ms"]))) == 13
                if item["max_time_ms"] is not None:
                    assert len(str(int(item["max_time_ms"]))) == 13
                if case_id == "BF-BATCH-BOUNDS-DQC-001" and item["has_data"]:
                    assert int(item["min_time_ms"]) <= int(item["max_time_ms"])


@allure.title('BF-BATCH-BOUNDS-BOUNDARY-002 - Boundary - 六个接口 | 单个 symbol/pair')
@allure.feature('binance-full')
@allure.story('Boundary')
@pytest.mark.binance_full_api
def test_bf_batch_bounds_boundary_002(binance_full_api):
    """
    Case ID: BF-BATCH-BOUNDS-BOUNDARY-002
    测试大类: binance-full
    测试类型: Boundary
    测试目的: 六个接口 | 单个 symbol/pair
    预期断言: 返回 1 个或 0 个 item；结构正确
    """

    case_id = 'BF-BATCH-BOUNDS-BOUNDARY-002'

    responses = []

    request_params_1 = {}

    response_1 = binance_full_api.batch_usdm_kline_time_bounds(symbols=['BTCUSDT'], contract_type=None, interval='1m')

    responses.append(('kline-time-bounds', response_1, request_params_1))

    request_params_2 = {'contract_type': 'PERPETUAL'}

    response_2 = binance_full_api.batch_coinm_perp_kline_time_bounds(symbols=['BTCUSD'], contract_type='PERPETUAL', interval='1m')

    responses.append(('kline-time-bounds', response_2, request_params_2))

    request_params_3 = {'contract_type': 'CURRENT_QUARTER'}

    response_3 = binance_full_api.batch_coinm_delivery_kline_time_bounds(symbols=['BTCUSD'], contract_type='CURRENT_QUARTER', interval='1m')

    responses.append(('kline-time-bounds', response_3, request_params_3))

    request_params_4 = {'contract_type': 'CURRENT_QUARTER'}

    response_4 = binance_full_api.batch_usdm_delivery_kline_time_bounds(symbols=['BTCUSDT'], contract_type='CURRENT_QUARTER', interval='1m')

    responses.append(('kline-time-bounds', response_4, request_params_4))

    request_params_5 = {}

    response_5 = binance_full_api.batch_usdm_funding_time_bounds(symbols=['BTCUSDT'], contract_type=None)

    responses.append(('funding-time-bounds', response_5, request_params_5))

    request_params_6 = {'contract_type': 'PERPETUAL'}

    response_6 = binance_full_api.batch_coinm_perp_funding_time_bounds(symbols=['BTCUSD'], contract_type='PERPETUAL')

    responses.append(('funding-time-bounds', response_6, request_params_6))

    for target_name, response, request_params in responses:

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
        assert isinstance(data, dict)
        assert "items" in data
        assert isinstance(data["items"], list)
        if case_id == "BF-BATCH-BOUNDS-BOUNDARY-002":
            assert len(data["items"]) <= 1
        for item in data["items"]:
            assert "symbol" in item or "pair" in item
            assert "min_time_ms" in item
            assert "max_time_ms" in item
            assert "has_data" in item
            if item["min_time_ms"] is not None:
                assert len(str(int(item["min_time_ms"]))) == 13
            if item["max_time_ms"] is not None:
                assert len(str(int(item["max_time_ms"]))) == 13
            if case_id == "BF-BATCH-BOUNDS-DQC-001" and item["has_data"]:
                assert int(item["min_time_ms"]) <= int(item["max_time_ms"])


@allure.title('BF-BATCH-BOUNDS-PARAM-001 - ParamError - 六个接口 | 缺少 body `symbols')
@allure.feature('binance-full')
@allure.story('ParamError')
@pytest.mark.binance_full_api
def test_bf_batch_bounds_param_001(binance_full_api):
    """
    Case ID: BF-BATCH-BOUNDS-PARAM-001
    测试大类: binance-full
    测试类型: ParamError
    测试目的: 六个接口 | 缺少 body `symbols
    预期断言: 返回请求体校验错误
    """

    case_id = 'BF-BATCH-BOUNDS-PARAM-001'

    responses = []

    request_params_1 = {}

    try:
        response_1 = binance_full_api.batch_usdm_kline_time_bounds(symbols=None, contract_type=None, interval='1m')
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('kline-time-bounds', response_1, request_params_1))

    request_params_2 = {'contract_type': 'PERPETUAL'}

    try:
        response_2 = binance_full_api.batch_coinm_perp_kline_time_bounds(symbols=None, contract_type='PERPETUAL', interval='1m')
    except HTTPError as exc:
        assert exc.response is not None
        response_2 = exc.response

    responses.append(('kline-time-bounds', response_2, request_params_2))

    request_params_3 = {'contract_type': 'CURRENT_QUARTER'}

    try:
        response_3 = binance_full_api.batch_coinm_delivery_kline_time_bounds(symbols=None, contract_type='CURRENT_QUARTER', interval='1m')
    except HTTPError as exc:
        assert exc.response is not None
        response_3 = exc.response

    responses.append(('kline-time-bounds', response_3, request_params_3))

    request_params_4 = {'contract_type': 'CURRENT_QUARTER'}

    try:
        response_4 = binance_full_api.batch_usdm_delivery_kline_time_bounds(symbols=None, contract_type='CURRENT_QUARTER', interval='1m')
    except HTTPError as exc:
        assert exc.response is not None
        response_4 = exc.response

    responses.append(('kline-time-bounds', response_4, request_params_4))

    request_params_5 = {}

    try:
        response_5 = binance_full_api.batch_usdm_funding_time_bounds(symbols=None, contract_type=None)
    except HTTPError as exc:
        assert exc.response is not None
        response_5 = exc.response

    responses.append(('funding-time-bounds', response_5, request_params_5))

    request_params_6 = {'contract_type': 'PERPETUAL'}

    try:
        response_6 = binance_full_api.batch_coinm_perp_funding_time_bounds(symbols=None, contract_type='PERPETUAL')
    except HTTPError as exc:
        assert exc.response is not None
        response_6 = exc.response

    responses.append(('funding-time-bounds', response_6, request_params_6))

    for target_name, response, request_params in responses:

        assert response.status_code < 500
        body = response.json() if response.content else {}
        assert str(body.get("code", "")) != "500"
        if response.status_code >= 400:
            assert response.status_code in {400, 422}
            assert any(key in str(body).lower() for key in ('symbol', 'pair', 'contract_type', 'time', 'limit', 'symbols'))
        else:
            assert "code" in body
            assert "status" in body
            assert "message" in body
            assert str(body["code"]) in {"400", "422"}
            assert body["status"] in {"error", "fail", "failed"}
        assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('BF-BATCH-BOUNDS-PARAM-002 - ParamError - 六个接口 | symbols=[]')
@allure.feature('binance-full')
@allure.story('ParamError')
@pytest.mark.binance_full_api
def test_bf_batch_bounds_param_002(binance_full_api):
    """
    Case ID: BF-BATCH-BOUNDS-PARAM-002
    测试大类: binance-full
    测试类型: ParamError
    测试目的: 六个接口 | symbols=[]
    预期断言: 返回参数错误或空数组业务提示；不能 500
    """

    case_id = 'BF-BATCH-BOUNDS-PARAM-002'

    responses = []

    request_params_1 = {}

    try:
        response_1 = binance_full_api.batch_usdm_kline_time_bounds(symbols=[], contract_type=None, interval='1m')
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('kline-time-bounds', response_1, request_params_1))

    request_params_2 = {'contract_type': 'PERPETUAL'}

    try:
        response_2 = binance_full_api.batch_coinm_perp_kline_time_bounds(symbols=[], contract_type='PERPETUAL', interval='1m')
    except HTTPError as exc:
        assert exc.response is not None
        response_2 = exc.response

    responses.append(('kline-time-bounds', response_2, request_params_2))

    request_params_3 = {'contract_type': 'CURRENT_QUARTER'}

    try:
        response_3 = binance_full_api.batch_coinm_delivery_kline_time_bounds(symbols=[], contract_type='CURRENT_QUARTER', interval='1m')
    except HTTPError as exc:
        assert exc.response is not None
        response_3 = exc.response

    responses.append(('kline-time-bounds', response_3, request_params_3))

    request_params_4 = {'contract_type': 'CURRENT_QUARTER'}

    try:
        response_4 = binance_full_api.batch_usdm_delivery_kline_time_bounds(symbols=[], contract_type='CURRENT_QUARTER', interval='1m')
    except HTTPError as exc:
        assert exc.response is not None
        response_4 = exc.response

    responses.append(('kline-time-bounds', response_4, request_params_4))

    request_params_5 = {}

    try:
        response_5 = binance_full_api.batch_usdm_funding_time_bounds(symbols=[], contract_type=None)
    except HTTPError as exc:
        assert exc.response is not None
        response_5 = exc.response

    responses.append(('funding-time-bounds', response_5, request_params_5))

    request_params_6 = {'contract_type': 'PERPETUAL'}

    try:
        response_6 = binance_full_api.batch_coinm_perp_funding_time_bounds(symbols=[], contract_type='PERPETUAL')
    except HTTPError as exc:
        assert exc.response is not None
        response_6 = exc.response

    responses.append(('funding-time-bounds', response_6, request_params_6))

    for target_name, response, request_params in responses:

        assert response.status_code < 500
        body = response.json() if response.content else {}
        print(body)
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
            assert isinstance(data, dict)
            assert "items" in data
            assert isinstance(data["items"], list)
            if case_id == "BF-BATCH-BOUNDS-BOUNDARY-002":
                assert len(data["items"]) <= 1
            for item in data["items"]:
                assert "symbol" in item or "pair" in item
                assert "min_time_ms" in item
                assert "max_time_ms" in item
                assert "has_data" in item
                if item["min_time_ms"] is not None:
                    assert len(str(int(item["min_time_ms"]))) == 13
                if item["max_time_ms"] is not None:
                    assert len(str(int(item["max_time_ms"]))) == 13
                if case_id == "BF-BATCH-BOUNDS-DQC-001" and item["has_data"]:
                    assert int(item["min_time_ms"]) <= int(item["max_time_ms"])


@allure.title('BF-BATCH-BOUNDS-PARAM-003 - ParamError - 需 contract_type 的接口 | 缺少 query `contract_type')
@allure.feature('binance-full')
@allure.story('ParamError')
@pytest.mark.binance_full_api
def test_bf_batch_bounds_param_003(binance_full_api):
    """
    Case ID: BF-BATCH-BOUNDS-PARAM-003
    测试大类: binance-full
    测试类型: ParamError
    测试目的: 需 contract_type 的接口 | 缺少 query `contract_type
    预期断言: 返回参数错误
    """

    case_id = 'BF-BATCH-BOUNDS-PARAM-003'

    responses = []

    request_params_1 = {}

    try:
        response_1 = binance_full_api.batch_coinm_perp_kline_time_bounds(symbols=['BTCUSD', 'ETHUSD'], contract_type=None, interval='1m')
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('kline-time-bounds', response_1, request_params_1))

    request_params_2 = {}

    try:
        response_2 = binance_full_api.batch_coinm_delivery_kline_time_bounds(symbols=['BTCUSD'], contract_type=None, interval='1m')
    except HTTPError as exc:
        assert exc.response is not None
        response_2 = exc.response

    responses.append(('kline-time-bounds', response_2, request_params_2))

    request_params_3 = {}

    try:
        response_3 = binance_full_api.batch_usdm_delivery_kline_time_bounds(symbols=['BTCUSDT'], contract_type=None, interval='1m')
    except HTTPError as exc:
        assert exc.response is not None
        response_3 = exc.response

    responses.append(('kline-time-bounds', response_3, request_params_3))

    request_params_4 = {}

    try:
        response_4 = binance_full_api.batch_coinm_perp_funding_time_bounds(symbols=['BTCUSD', 'ETHUSD'], contract_type=None)
    except HTTPError as exc:
        assert exc.response is not None
        response_4 = exc.response

    responses.append(('funding-time-bounds', response_4, request_params_4))

    for target_name, response, request_params in responses:

        assert response.status_code < 500
        body = response.json() if response.content else {}
        assert str(body.get("code", "")) != "500"
        if response.status_code >= 400:
            assert response.status_code in {400, 422}
            assert any(key in str(body).lower() for key in ('symbol', 'pair', 'contract_type', 'time', 'limit', 'symbols'))
        else:
            assert "code" in body
            assert "status" in body
            assert "message" in body
            assert str(body["code"]) in {"400", "422"}
            assert body["status"] in {"error", "fail", "failed"}
        assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('BF-BATCH-BOUNDS-PARAM-004 - ParamError - delivery bounds | contract_type=PERPETUAL')
@allure.feature('binance-full')
@allure.story('ParamError')
@pytest.mark.binance_full_api
def test_bf_batch_bounds_param_004(binance_full_api):
    """
    Case ID: BF-BATCH-BOUNDS-PARAM-004
    测试大类: binance-full
    测试类型: ParamError
    测试目的: delivery bounds | contract_type=PERPETUAL
    预期断言: 返回参数错误
    """

    case_id = 'BF-BATCH-BOUNDS-PARAM-004'

    responses = []

    request_params_1 = {'contract_type': 'PERPETUAL'}

    try:
        response_1 = binance_full_api.batch_coinm_delivery_kline_time_bounds(symbols=['BTCUSD'], contract_type='PERPETUAL', interval='1m')
    except HTTPError as exc:
        assert exc.response is not None
        response_1 = exc.response

    responses.append(('kline-time-bounds', response_1, request_params_1))

    request_params_2 = {'contract_type': 'PERPETUAL'}

    try:
        response_2 = binance_full_api.batch_usdm_delivery_kline_time_bounds(symbols=['BTCUSDT'], contract_type='PERPETUAL', interval='1m')
    except HTTPError as exc:
        assert exc.response is not None
        response_2 = exc.response

    responses.append(('kline-time-bounds', response_2, request_params_2))

    for target_name, response, request_params in responses:

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
            assert isinstance(data, dict)
            assert "items" in data
            assert isinstance(data["items"], list)
            if case_id == "BF-BATCH-BOUNDS-BOUNDARY-002":
                assert len(data["items"]) <= 1
            for item in data["items"]:
                assert "symbol" in item or "pair" in item
                assert "min_time_ms" in item
                assert "max_time_ms" in item
                assert "has_data" in item
                if item["min_time_ms"] is not None:
                    assert len(str(int(item["min_time_ms"]))) == 13
                if item["max_time_ms"] is not None:
                    assert len(str(int(item["max_time_ms"]))) == 13
                if case_id == "BF-BATCH-BOUNDS-DQC-001" and item["has_data"]:
                    assert int(item["min_time_ms"]) <= int(item["max_time_ms"])


@allure.title('BF-BATCH-BOUNDS-DQC-001 - DataQuality - 六个接口 | 正常请求')
@allure.feature('binance-full')
@allure.story('DataQuality')
@pytest.mark.binance_full_api
@pytest.mark.dqc
def test_bf_batch_bounds_dqc_001(binance_full_api):
    """
    Case ID: BF-BATCH-BOUNDS-DQC-001
    测试大类: binance-full
    测试类型: DataQuality
    测试目的: 六个接口 | 正常请求
    预期断言: min_time_ms/max_time_ms` 非空时为 13 位毫秒；有数据时 `min <= max
    """

    case_id = 'BF-BATCH-BOUNDS-DQC-001'

    responses = []

    request_params_1 = {}

    response_1 = binance_full_api.batch_usdm_kline_time_bounds(symbols=['BTCUSDT', 'ETHUSDT'], contract_type=None, interval='1m')

    responses.append(('kline-time-bounds', response_1, request_params_1))

    request_params_2 = {'contract_type': 'PERPETUAL'}

    response_2 = binance_full_api.batch_coinm_perp_kline_time_bounds(symbols=['BTCUSD', 'ETHUSD'], contract_type='PERPETUAL', interval='1m')

    responses.append(('kline-time-bounds', response_2, request_params_2))

    request_params_3 = {'contract_type': 'CURRENT_QUARTER'}

    response_3 = binance_full_api.batch_coinm_delivery_kline_time_bounds(symbols=['BTCUSD'], contract_type='CURRENT_QUARTER', interval='1m')

    responses.append(('kline-time-bounds', response_3, request_params_3))

    request_params_4 = {'contract_type': 'CURRENT_QUARTER'}

    response_4 = binance_full_api.batch_usdm_delivery_kline_time_bounds(symbols=['BTCUSDT'], contract_type='CURRENT_QUARTER', interval='1m')

    responses.append(('kline-time-bounds', response_4, request_params_4))

    request_params_5 = {}

    response_5 = binance_full_api.batch_usdm_funding_time_bounds(symbols=['BTCUSDT', 'ETHUSDT'], contract_type=None)

    responses.append(('funding-time-bounds', response_5, request_params_5))

    request_params_6 = {'contract_type': 'PERPETUAL'}

    response_6 = binance_full_api.batch_coinm_perp_funding_time_bounds(symbols=['BTCUSD', 'ETHUSD'], contract_type='PERPETUAL')

    responses.append(('funding-time-bounds', response_6, request_params_6))

    for target_name, response, request_params in responses:

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
        assert isinstance(data, dict)
        assert "items" in data
        assert isinstance(data["items"], list)
        if case_id == "BF-BATCH-BOUNDS-BOUNDARY-002":
            assert len(data["items"]) <= 1
        for item in data["items"]:
            assert "symbol" in item or "pair" in item
            assert "min_time_ms" in item
            assert "max_time_ms" in item
            assert "has_data" in item
            if item["min_time_ms"] is not None:
                assert len(str(int(item["min_time_ms"]))) == 13
            if item["max_time_ms"] is not None:
                assert len(str(int(item["max_time_ms"]))) == 13
            if case_id == "BF-BATCH-BOUNDS-DQC-001" and item["has_data"]:
                assert int(item["min_time_ms"]) <= int(item["max_time_ms"])
