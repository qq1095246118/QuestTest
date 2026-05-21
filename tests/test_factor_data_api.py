from __future__ import annotations

from decimal import Decimal

import allure
import pytest
from requests.exceptions import HTTPError

from api.platform.factor_data_api import FactorDataAPI
from config.settings import settings

SYMBOLS_SINGLE = ["BTCUSDT"]
SYMBOLS_MULTI = ["BTCUSDT", "ETHUSDT"]
START_TIME_MS = 1704067200000
END_TIME_MS = 1704153600000
INTERVAL_1M = "1m"
INTERVAL_1H = "1h"
QUALITY_FLAGS = ["OK"]
PAGE_SIZE_SMALL = 1
PAGE_SIZE_NORMAL = 100
PAGE_SIZE_MAX = 5000


@pytest.fixture(scope="module")
def factor_data_api() -> FactorDataAPI:
    if not settings.base_url or "exchange.com" in settings.base_url:
        pytest.skip("Factor Data API BASE_URL is not configured for live API tests.")
    return FactorDataAPI()


@allure.title('FD-DATASET-KLINE-FUTURE-NORMAL-001 - Normal - dataset=kline_data_future`，`symbols=["BTCUSDT"]`，`interval=1m`，`quality_flags=["OK"]')
@allure.feature('factor-data')
@allure.story('Normal')
@pytest.mark.factor_data_api
def test_fd_dataset_kline_future_normal_001(factor_data_api):
    """
    Case ID: FD-DATASET-KLINE-FUTURE-NORMAL-001
    测试大类: factor-data
    测试类型: Normal
    测试目的: dataset=kline_data_future`，`symbols=["BTCUSDT"]`，`interval=1m`，`quality_flags=["OK"]
    预期断言: 成功；rows 为期货 K 线清洗数据；时间字段在窗口内
    """

    params = {'dataset': 'kline_data_future', 'symbols': ['BTCUSDT'], 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'quality_flags': ['OK'], 'page_size': 100, 'sort': 'asc', 'include_symbol_coverage': False}

    response = factor_data_api.query(**params)

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
    assert "query" in data
    assert "rows" in data
    assert "next_cursor" in data
    assert "has_more" in data
    assert "row_count_returned" in data
    assert isinstance(data["rows"], list)
    assert isinstance(data["has_more"], bool)
    assert data["row_count_returned"] == len(data["rows"])
    if params.get("page_size") is not None:
        assert len(data["rows"]) <= int(params["page_size"])
    if "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    query = data["query"]
    if "dataset" in params and query.get("dataset") is not None:
        assert query["dataset"] == params["dataset"]
    if "dataset" not in params and query.get("dataset") is not None:
        assert query["dataset"] == "kline_data_future"
    if "symbols" in params and isinstance(params["symbols"], list):
        for row in data["rows"]:
            if "symbol" in row:
                assert row["symbol"] in params["symbols"]
    timestamps = []
    requested_fields = params.get("fields")
    for row in data["rows"]:
        time_value = row.get("timestamp", row.get("time", row.get("funding_time")))
        if time_value is not None:
            millis = int(time_value)
            timestamps.append(millis)
            assert len(str(millis)) == 13
            if params.get("start_time_ms") is not None and params.get("end_time_ms") is not None and len(str(int(params["start_time_ms"]))) == 13:
                assert params["start_time_ms"] <= millis < params["end_time_ms"]
        for numeric_field in ("open", "high", "low", "close", "volume", "funding_rate", "open_interest"):
            if numeric_field in row and row[numeric_field] is not None:
                assert Decimal(str(row[numeric_field])) is not None
        if requested_fields:
            allowed_fields = set(requested_fields) | {"dataset", "quality_flag", "created_at", "updated_at"}
            assert set(row).issubset(allowed_fields)
        if params.get("quality_flags") and "quality_flag" in row:
            assert str(row["quality_flag"]).upper() in {flag.upper() for flag in params["quality_flags"]}


@allure.title('FD-DATASET-KLINE-SPOT-NORMAL-001 - Normal - dataset=kline_data_spot`，`symbols=["BTCUSDT"]`，`interval=1m`，`quality_flags=["OK"]')
@allure.feature('factor-data')
@allure.story('Normal')
@pytest.mark.factor_data_api
def test_fd_dataset_kline_spot_normal_001(factor_data_api):
    """
    Case ID: FD-DATASET-KLINE-SPOT-NORMAL-001
    测试大类: factor-data
    测试类型: Normal
    测试目的: dataset=kline_data_spot`，`symbols=["BTCUSDT"]`，`interval=1m`，`quality_flags=["OK"]
    预期断言: 成功；rows 为 Spot K 线清洗数据
    """

    params = {'dataset': 'kline_data_spot', 'symbols': ['BTCUSDT'], 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'quality_flags': ['OK'], 'page_size': 100, 'sort': 'asc', 'include_symbol_coverage': False}

    response = factor_data_api.query(**params)

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
    assert "query" in data
    assert "rows" in data
    assert "next_cursor" in data
    assert "has_more" in data
    assert "row_count_returned" in data
    assert isinstance(data["rows"], list)
    assert isinstance(data["has_more"], bool)
    assert data["row_count_returned"] == len(data["rows"])
    if params.get("page_size") is not None:
        assert len(data["rows"]) <= int(params["page_size"])
    if "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    query = data["query"]
    if "dataset" in params and query.get("dataset") is not None:
        assert query["dataset"] == params["dataset"]
    if "dataset" not in params and query.get("dataset") is not None:
        assert query["dataset"] == "kline_data_future"
    if "symbols" in params and isinstance(params["symbols"], list):
        for row in data["rows"]:
            if "symbol" in row:
                assert row["symbol"] in params["symbols"]
    timestamps = []
    requested_fields = params.get("fields")
    for row in data["rows"]:
        time_value = row.get("timestamp", row.get("time", row.get("funding_time")))
        if time_value is not None:
            millis = int(time_value)
            timestamps.append(millis)
            assert len(str(millis)) == 13
            if params.get("start_time_ms") is not None and params.get("end_time_ms") is not None and len(str(int(params["start_time_ms"]))) == 13:
                assert params["start_time_ms"] <= millis < params["end_time_ms"]
        for numeric_field in ("open", "high", "low", "close", "volume", "funding_rate", "open_interest"):
            if numeric_field in row and row[numeric_field] is not None:
                assert Decimal(str(row[numeric_field])) is not None
        if requested_fields:
            allowed_fields = set(requested_fields) | {"dataset", "quality_flag", "created_at", "updated_at"}
            assert set(row).issubset(allowed_fields)
        if params.get("quality_flags") and "quality_flag" in row:
            assert str(row["quality_flag"]).upper() in {flag.upper() for flag in params["quality_flags"]}


@allure.title('FD-DATASET-BINANCE-FUNDING-NORMAL-001 - Normal - dataset=binance_usdm_funding_rate_clean`，`interval=null` 或省略')
@allure.feature('factor-data')
@allure.story('Normal')
@pytest.mark.factor_data_api
def test_fd_dataset_binance_funding_normal_001(factor_data_api):
    """
    Case ID: FD-DATASET-BINANCE-FUNDING-NORMAL-001
    测试大类: factor-data
    测试类型: Normal
    测试目的: dataset=binance_usdm_funding_rate_clean`，`interval=null` 或省略
    预期断言: 成功；rows 为 funding 数据；不依赖 interval
    """

    params = {'dataset': 'binance_usdm_funding_rate_clean', 'symbols': ['BTCUSDT'], 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'page_size': 100, 'sort': 'asc', 'include_symbol_coverage': False}

    response = factor_data_api.query(**params)

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
    assert "query" in data
    assert "rows" in data
    assert "next_cursor" in data
    assert "has_more" in data
    assert "row_count_returned" in data
    assert isinstance(data["rows"], list)
    assert isinstance(data["has_more"], bool)
    assert data["row_count_returned"] == len(data["rows"])
    if params.get("page_size") is not None:
        assert len(data["rows"]) <= int(params["page_size"])
    if "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    query = data["query"]
    if "dataset" in params and query.get("dataset") is not None:
        assert query["dataset"] == params["dataset"]
    if "dataset" not in params and query.get("dataset") is not None:
        assert query["dataset"] == "kline_data_future"
    if "symbols" in params and isinstance(params["symbols"], list):
        for row in data["rows"]:
            if "symbol" in row:
                assert row["symbol"] in params["symbols"]
    timestamps = []
    requested_fields = params.get("fields")
    for row in data["rows"]:
        time_value = row.get("timestamp", row.get("time", row.get("funding_time")))
        if time_value is not None:
            millis = int(time_value)
            timestamps.append(millis)
            assert len(str(millis)) == 13
            if params.get("start_time_ms") is not None and params.get("end_time_ms") is not None and len(str(int(params["start_time_ms"]))) == 13:
                assert params["start_time_ms"] <= millis < params["end_time_ms"]
        for numeric_field in ("open", "high", "low", "close", "volume", "funding_rate", "open_interest"):
            if numeric_field in row and row[numeric_field] is not None:
                assert Decimal(str(row[numeric_field])) is not None
        if requested_fields:
            allowed_fields = set(requested_fields) | {"dataset", "quality_flag", "created_at", "updated_at"}
            assert set(row).issubset(allowed_fields)
        if params.get("quality_flags") and "quality_flag" in row:
            assert str(row["quality_flag"]).upper() in {flag.upper() for flag in params["quality_flags"]}


@allure.title('FD-DATASET-OI-NORMAL-001 - Normal - dataset=coinglass_open_interest_clean`，`interval=1h')
@allure.feature('factor-data')
@allure.story('Normal')
@pytest.mark.factor_data_api
def test_fd_dataset_oi_normal_001(factor_data_api):
    """
    Case ID: FD-DATASET-OI-NORMAL-001
    测试大类: factor-data
    测试类型: Normal
    测试目的: dataset=coinglass_open_interest_clean`，`interval=1h
    预期断言: 成功；rows 为 OI 清洗数据或空窗提示
    """

    params = {'dataset': 'coinglass_open_interest_clean', 'symbols': ['BTCUSDT'], 'interval': '1h', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'page_size': 100, 'sort': 'asc', 'include_symbol_coverage': False}

    response = factor_data_api.query(**params)

    assert response.status_code == 200
    body = response.json()
    assert "code" in body
    assert "status" in body
    assert "message" in body
    assert "data" in body
    if str(body["code"]) == "400":
        assert body["status"] == "error"
        assert body["message"]
        data = body["data"]
        assert "query" in data
        assert "rows" in data
        assert "next_cursor" in data
        assert "has_more" in data
        assert "row_count_returned" in data
        assert data["rows"] == []
        assert data["has_more"] is False
        assert data["row_count_returned"] == 0
        assert data["earliest_available_time_ms"] is not None
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
        return
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]
    data = body["data"]
    assert data is not None
    assert "query" in data
    assert "rows" in data
    assert "next_cursor" in data
    assert "has_more" in data
    assert "row_count_returned" in data
    assert isinstance(data["rows"], list)
    assert isinstance(data["has_more"], bool)
    assert data["row_count_returned"] == len(data["rows"])
    if params.get("page_size") is not None:
        assert len(data["rows"]) <= int(params["page_size"])
    if "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    query = data["query"]
    if "dataset" in params and query.get("dataset") is not None:
        assert query["dataset"] == params["dataset"]
    if "dataset" not in params and query.get("dataset") is not None:
        assert query["dataset"] == "kline_data_future"
    if "symbols" in params and isinstance(params["symbols"], list):
        for row in data["rows"]:
            if "symbol" in row:
                assert row["symbol"] in params["symbols"]
    timestamps = []
    requested_fields = params.get("fields")
    for row in data["rows"]:
        time_value = row.get("timestamp", row.get("time", row.get("funding_time")))
        if time_value is not None:
            millis = int(time_value)
            timestamps.append(millis)
            assert len(str(millis)) == 13
            if params.get("start_time_ms") is not None and params.get("end_time_ms") is not None and len(str(int(params["start_time_ms"]))) == 13:
                assert params["start_time_ms"] <= millis < params["end_time_ms"]
        for numeric_field in ("open", "high", "low", "close", "volume", "funding_rate", "open_interest"):
            if numeric_field in row and row[numeric_field] is not None:
                assert Decimal(str(row[numeric_field])) is not None
        if requested_fields:
            allowed_fields = set(requested_fields) | {"dataset", "quality_flag", "created_at", "updated_at"}
            assert set(row).issubset(allowed_fields)
        if params.get("quality_flags") and "quality_flag" in row:
            assert str(row["quality_flag"]).upper() in {flag.upper() for flag in params["quality_flags"]}


@allure.title('FD-DATASET-LS-NORMAL-001 - Normal - dataset=coinglass_global_long_short_account_ratio_clean`，`interval=1h')
@allure.feature('factor-data')
@allure.story('Normal')
@pytest.mark.factor_data_api
def test_fd_dataset_ls_normal_001(factor_data_api):
    """
    Case ID: FD-DATASET-LS-NORMAL-001
    测试大类: factor-data
    测试类型: Normal
    测试目的: dataset=coinglass_global_long_short_account_ratio_clean`，`interval=1h
    预期断言: 成功；rows 为多空比清洗数据或空窗提示
    """

    params = {'dataset': 'coinglass_global_long_short_account_ratio_clean', 'symbols': ['BTCUSDT'], 'interval': '1h', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'page_size': 100, 'sort': 'asc', 'include_symbol_coverage': False}

    response = factor_data_api.query(**params)

    assert response.status_code == 200
    body = response.json()
    assert "code" in body
    assert "status" in body
    assert "message" in body
    assert "data" in body
    if str(body["code"]) == "400":
        assert body["status"] == "error"
        assert body["message"]
        data = body["data"]
        assert "query" in data
        assert "rows" in data
        assert "next_cursor" in data
        assert "has_more" in data
        assert "row_count_returned" in data
        assert data["rows"] == []
        assert data["has_more"] is False
        assert data["row_count_returned"] == 0
        assert data["earliest_available_time_ms"] is not None
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
        return
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]
    data = body["data"]
    assert data is not None
    assert "query" in data
    assert "rows" in data
    assert "next_cursor" in data
    assert "has_more" in data
    assert "row_count_returned" in data
    assert isinstance(data["rows"], list)
    assert isinstance(data["has_more"], bool)
    assert data["row_count_returned"] == len(data["rows"])
    if params.get("page_size") is not None:
        assert len(data["rows"]) <= int(params["page_size"])
    if "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    query = data["query"]
    if "dataset" in params and query.get("dataset") is not None:
        assert query["dataset"] == params["dataset"]
    if "dataset" not in params and query.get("dataset") is not None:
        assert query["dataset"] == "kline_data_future"
    if "symbols" in params and isinstance(params["symbols"], list):
        for row in data["rows"]:
            if "symbol" in row:
                assert row["symbol"] in params["symbols"]
    timestamps = []
    requested_fields = params.get("fields")
    for row in data["rows"]:
        time_value = row.get("timestamp", row.get("time", row.get("funding_time")))
        if time_value is not None:
            millis = int(time_value)
            timestamps.append(millis)
            assert len(str(millis)) == 13
            if params.get("start_time_ms") is not None and params.get("end_time_ms") is not None and len(str(int(params["start_time_ms"]))) == 13:
                assert params["start_time_ms"] <= millis < params["end_time_ms"]
        for numeric_field in ("open", "high", "low", "close", "volume", "funding_rate", "open_interest"):
            if numeric_field in row and row[numeric_field] is not None:
                assert Decimal(str(row[numeric_field])) is not None
        if requested_fields:
            allowed_fields = set(requested_fields) | {"dataset", "quality_flag", "created_at", "updated_at"}
            assert set(row).issubset(allowed_fields)
        if params.get("quality_flags") and "quality_flag" in row:
            assert str(row["quality_flag"]).upper() in {flag.upper() for flag in params["quality_flags"]}


@allure.title('FD-DATASET-TAKER-NORMAL-001 - Normal - dataset=coinglass_aggregated_taker_buy_sell_volume_clean`，`interval=1h')
@allure.feature('factor-data')
@allure.story('Normal')
@pytest.mark.factor_data_api
def test_fd_dataset_taker_normal_001(factor_data_api):
    """
    Case ID: FD-DATASET-TAKER-NORMAL-001
    测试大类: factor-data
    测试类型: Normal
    测试目的: dataset=coinglass_aggregated_taker_buy_sell_volume_clean`，`interval=1h
    预期断言: 成功；rows 为聚合买卖量清洗数据或空窗提示
    """

    params = {'dataset': 'coinglass_aggregated_taker_buy_sell_volume_clean', 'symbols': ['BTCUSDT'], 'interval': '1h', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'page_size': 100, 'sort': 'asc', 'include_symbol_coverage': False}

    response = factor_data_api.query(**params)

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
    assert "query" in data
    assert "rows" in data
    assert "next_cursor" in data
    assert "has_more" in data
    assert "row_count_returned" in data
    assert isinstance(data["rows"], list)
    assert isinstance(data["has_more"], bool)
    assert data["row_count_returned"] == len(data["rows"])
    if params.get("page_size") is not None:
        assert len(data["rows"]) <= int(params["page_size"])
    if "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    query = data["query"]
    if "dataset" in params and query.get("dataset") is not None:
        assert query["dataset"] == params["dataset"]
    if "dataset" not in params and query.get("dataset") is not None:
        assert query["dataset"] == "kline_data_future"
    if "symbols" in params and isinstance(params["symbols"], list):
        for row in data["rows"]:
            if "symbol" in row:
                assert row["symbol"] in params["symbols"]
    timestamps = []
    requested_fields = params.get("fields")
    for row in data["rows"]:
        time_value = row.get("timestamp", row.get("time", row.get("funding_time")))
        if time_value is not None:
            millis = int(time_value)
            timestamps.append(millis)
            assert len(str(millis)) == 13
            if params.get("start_time_ms") is not None and params.get("end_time_ms") is not None and len(str(int(params["start_time_ms"]))) == 13:
                assert params["start_time_ms"] <= millis < params["end_time_ms"]
        for numeric_field in ("open", "high", "low", "close", "volume", "funding_rate", "open_interest"):
            if numeric_field in row and row[numeric_field] is not None:
                assert Decimal(str(row[numeric_field])) is not None
        if requested_fields:
            allowed_fields = set(requested_fields) | {"dataset", "quality_flag", "created_at", "updated_at"}
            assert set(row).issubset(allowed_fields)
        if params.get("quality_flags") and "quality_flag" in row:
            assert str(row["quality_flag"]).upper() in {flag.upper() for flag in params["quality_flags"]}


@allure.title('FD-DATASET-PARAM-001 - ParamError - dataset=unknown_dataset')
@allure.feature('factor-data')
@allure.story('ParamError')
@pytest.mark.factor_data_api
def test_fd_dataset_param_001(factor_data_api):
    """
    Case ID: FD-DATASET-PARAM-001
    测试大类: factor-data
    测试类型: ParamError
    测试目的: dataset=unknown_dataset
    预期断言: 返回枚举参数错误
    """

    params = {'dataset': 'unknown_dataset', 'symbols': ['BTCUSDT'], 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'page_size': 100, 'sort': 'asc', 'include_symbol_coverage': False}

    try:
        response = factor_data_api.query(**params)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert any(key in str(body).lower() for key in ('dataset', 'symbols', 'time', 'page_size', 'sort', 'field', 'quality'))
    else:
        assert "code" in body
        assert "status" in body
        assert "message" in body
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] in {"error", "fail", "failed"}
    assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('FD-DATASET-BOUNDARY-001 - Boundary - 缺少 `dataset')
@allure.feature('factor-data')
@allure.story('Boundary')
@pytest.mark.factor_data_api
def test_fd_dataset_boundary_001(factor_data_api):
    """
    Case ID: FD-DATASET-BOUNDARY-001
    测试大类: factor-data
    测试类型: Boundary
    测试目的: 缺少 `dataset
    预期断言: 按 OpenAPI 默认值使用 `kline_data_future`；响应成功时 `data.query` 应能体现默认 dataset 语义
    """

    params = {'symbols': ['BTCUSDT'], 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'page_size': 100, 'sort': 'asc', 'include_symbol_coverage': False}

    response = factor_data_api.query(**params)

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
    assert "query" in data
    assert "rows" in data
    assert "next_cursor" in data
    assert "has_more" in data
    assert "row_count_returned" in data
    assert isinstance(data["rows"], list)
    assert isinstance(data["has_more"], bool)
    assert data["row_count_returned"] == len(data["rows"])
    if params.get("page_size") is not None:
        assert len(data["rows"]) <= int(params["page_size"])
    if "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    query = data["query"]
    if "dataset" in params and query.get("dataset") is not None:
        assert query["dataset"] == params["dataset"]
    if "dataset" not in params and query.get("dataset") is not None:
        assert query["dataset"] == "kline_data_future"
    if "symbols" in params and isinstance(params["symbols"], list):
        for row in data["rows"]:
            if "symbol" in row:
                assert row["symbol"] in params["symbols"]
    timestamps = []
    requested_fields = params.get("fields")
    for row in data["rows"]:
        time_value = row.get("timestamp", row.get("time", row.get("funding_time")))
        if time_value is not None:
            millis = int(time_value)
            timestamps.append(millis)
            assert len(str(millis)) == 13
            if params.get("start_time_ms") is not None and params.get("end_time_ms") is not None and len(str(int(params["start_time_ms"]))) == 13:
                assert params["start_time_ms"] <= millis < params["end_time_ms"]
        for numeric_field in ("open", "high", "low", "close", "volume", "funding_rate", "open_interest"):
            if numeric_field in row and row[numeric_field] is not None:
                assert Decimal(str(row[numeric_field])) is not None
        if requested_fields:
            allowed_fields = set(requested_fields) | {"dataset", "quality_flag", "created_at", "updated_at"}
            assert set(row).issubset(allowed_fields)
        if params.get("quality_flags") and "quality_flag" in row:
            assert str(row["quality_flag"]).upper() in {flag.upper() for flag in params["quality_flags"]}


@allure.title('FD-SYMBOLS-NORMAL-001 - Normal - symbols=["BTCUSDT"]')
@allure.feature('factor-data')
@allure.story('Normal')
@pytest.mark.factor_data_api
def test_fd_symbols_normal_001(factor_data_api):
    """
    Case ID: FD-SYMBOLS-NORMAL-001
    测试大类: factor-data
    测试类型: Normal
    测试目的: symbols=["BTCUSDT"]
    预期断言: 成功；rows 中 symbol 与请求一致
    """

    params = {'dataset': 'kline_data_future', 'symbols': ['BTCUSDT'], 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'page_size': 100, 'sort': 'asc', 'include_symbol_coverage': False}

    response = factor_data_api.query(**params)

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
    assert "query" in data
    assert "rows" in data
    assert "next_cursor" in data
    assert "has_more" in data
    assert "row_count_returned" in data
    assert isinstance(data["rows"], list)
    assert isinstance(data["has_more"], bool)
    assert data["row_count_returned"] == len(data["rows"])
    if params.get("page_size") is not None:
        assert len(data["rows"]) <= int(params["page_size"])
    if "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    query = data["query"]
    if "dataset" in params and query.get("dataset") is not None:
        assert query["dataset"] == params["dataset"]
    if "dataset" not in params and query.get("dataset") is not None:
        assert query["dataset"] == "kline_data_future"
    if "symbols" in params and isinstance(params["symbols"], list):
        for row in data["rows"]:
            if "symbol" in row:
                assert row["symbol"] in params["symbols"]
    timestamps = []
    requested_fields = params.get("fields")
    for row in data["rows"]:
        time_value = row.get("timestamp", row.get("time", row.get("funding_time")))
        if time_value is not None:
            millis = int(time_value)
            timestamps.append(millis)
            assert len(str(millis)) == 13
            if params.get("start_time_ms") is not None and params.get("end_time_ms") is not None and len(str(int(params["start_time_ms"]))) == 13:
                assert params["start_time_ms"] <= millis < params["end_time_ms"]
        for numeric_field in ("open", "high", "low", "close", "volume", "funding_rate", "open_interest"):
            if numeric_field in row and row[numeric_field] is not None:
                assert Decimal(str(row[numeric_field])) is not None
        if requested_fields:
            allowed_fields = set(requested_fields) | {"dataset", "quality_flag", "created_at", "updated_at"}
            assert set(row).issubset(allowed_fields)
        if params.get("quality_flags") and "quality_flag" in row:
            assert str(row["quality_flag"]).upper() in {flag.upper() for flag in params["quality_flags"]}


@allure.title('FD-SYMBOLS-NORMAL-002 - Normal - symbols=["BTCUSDT","ETHUSDT"]')
@allure.feature('factor-data')
@allure.story('Normal')
@pytest.mark.factor_data_api
def test_fd_symbols_normal_002(factor_data_api):
    """
    Case ID: FD-SYMBOLS-NORMAL-002
    测试大类: factor-data
    测试类型: Normal
    测试目的: symbols=["BTCUSDT","ETHUSDT"]
    预期断言: 成功；rows symbol 均在请求集合内；coverage 可按 symbol 分组
    """

    params = {'dataset': 'kline_data_future', 'symbols': ['BTCUSDT', 'ETHUSDT'], 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'page_size': 100, 'sort': 'asc', 'include_symbol_coverage': True}

    response = factor_data_api.query(**params)

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
    assert "query" in data
    assert "rows" in data
    assert "next_cursor" in data
    assert "has_more" in data
    assert "row_count_returned" in data
    assert isinstance(data["rows"], list)
    assert isinstance(data["has_more"], bool)
    assert data["row_count_returned"] == len(data["rows"])
    if params.get("page_size") is not None:
        assert len(data["rows"]) <= int(params["page_size"])
    if "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    query = data["query"]
    if "dataset" in params and query.get("dataset") is not None:
        assert query["dataset"] == params["dataset"]
    if "dataset" not in params and query.get("dataset") is not None:
        assert query["dataset"] == "kline_data_future"
    if "symbols" in params and isinstance(params["symbols"], list):
        for row in data["rows"]:
            if "symbol" in row:
                assert row["symbol"] in params["symbols"]
    timestamps = []
    requested_fields = params.get("fields")
    for row in data["rows"]:
        time_value = row.get("timestamp", row.get("time", row.get("funding_time")))
        if time_value is not None:
            millis = int(time_value)
            timestamps.append(millis)
            assert len(str(millis)) == 13
            if params.get("start_time_ms") is not None and params.get("end_time_ms") is not None and len(str(int(params["start_time_ms"]))) == 13:
                assert params["start_time_ms"] <= millis < params["end_time_ms"]
        for numeric_field in ("open", "high", "low", "close", "volume", "funding_rate", "open_interest"):
            if numeric_field in row and row[numeric_field] is not None:
                assert Decimal(str(row[numeric_field])) is not None
        if requested_fields:
            allowed_fields = set(requested_fields) | {"dataset", "quality_flag", "created_at", "updated_at"}
            assert set(row).issubset(allowed_fields)
        if params.get("quality_flags") and "quality_flag" in row:
            assert str(row["quality_flag"]).upper() in {flag.upper() for flag in params["quality_flags"]}


@allure.title('FD-SYMBOLS-PARAM-001 - ParamError - symbols=[]')
@allure.feature('factor-data')
@allure.story('ParamError')
@pytest.mark.factor_data_api
def test_fd_symbols_param_001(factor_data_api):
    """
    Case ID: FD-SYMBOLS-PARAM-001
    测试大类: factor-data
    测试类型: ParamError
    测试目的: symbols=[]
    预期断言: 返回 `minItems` 校验错误
    """

    params = {'dataset': 'kline_data_future', 'symbols': [], 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'page_size': 100, 'sort': 'asc', 'include_symbol_coverage': False}

    try:
        response = factor_data_api.query(**params)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert any(key in str(body).lower() for key in ('dataset', 'symbols', 'time', 'page_size', 'sort', 'field', 'quality'))
    else:
        assert "code" in body
        assert "status" in body
        assert "message" in body
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] in {"error", "fail", "failed"}
    assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('FD-SYMBOLS-PARAM-002 - ParamError - 缺少 `symbols')
@allure.feature('factor-data')
@allure.story('ParamError')
@pytest.mark.factor_data_api
def test_fd_symbols_param_002(factor_data_api):
    """
    Case ID: FD-SYMBOLS-PARAM-002
    测试大类: factor-data
    测试类型: ParamError
    测试目的: 缺少 `symbols
    预期断言: 返回请求体校验错误
    """

    params = {'dataset': 'kline_data_future', 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'page_size': 100, 'sort': 'asc', 'include_symbol_coverage': False}

    try:
        response = factor_data_api.query(**params)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert any(key in str(body).lower() for key in ('dataset', 'symbols', 'time', 'page_size', 'sort', 'field', 'quality'))
    else:
        assert "code" in body
        assert "status" in body
        assert "message" in body
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] in {"error", "fail", "failed"}
    assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('FD-SYMBOLS-PARAM-003 - ParamError - symbols="BTCUSDT"')
@allure.feature('factor-data')
@allure.story('ParamError')
@pytest.mark.factor_data_api
def test_fd_symbols_param_003(factor_data_api):
    """
    Case ID: FD-SYMBOLS-PARAM-003
    测试大类: factor-data
    测试类型: ParamError
    测试目的: symbols="BTCUSDT"
    预期断言: 返回类型错误，必须是数组
    """

    params = {'dataset': 'kline_data_future', 'symbols': 'BTCUSDT', 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'page_size': 100, 'sort': 'asc', 'include_symbol_coverage': False}

    try:
        response = factor_data_api.query(**params)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert any(key in str(body).lower() for key in ('dataset', 'symbols', 'time', 'page_size', 'sort', 'field', 'quality'))
    else:
        assert "code" in body
        assert "status" in body
        assert "message" in body
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] in {"error", "fail", "failed"}
    assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('FD-SYMBOLS-PARAM-004 - ParamError - symbols=["not_lower_case"]')
@allure.feature('factor-data')
@allure.story('ParamError')
@pytest.mark.factor_data_api
def test_fd_symbols_param_004(factor_data_api):
    """
    Case ID: FD-SYMBOLS-PARAM-004
    测试大类: factor-data
    测试类型: ParamError
    测试目的: symbols=["not_lower_case"]
    预期断言: 返回空结果、覆盖率无数据或业务提示；不能 500
    """

    params = {'dataset': 'kline_data_future', 'symbols': ['not_lower_case'], 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'page_size': 100, 'sort': 'asc', 'include_symbol_coverage': False}

    try:
        response = factor_data_api.query(**params)
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
        assert "query" in data
        assert "rows" in data
        assert "next_cursor" in data
        assert "has_more" in data
        assert "row_count_returned" in data
        assert isinstance(data["rows"], list)
        assert isinstance(data["has_more"], bool)
        assert data["row_count_returned"] == len(data["rows"])
        if params.get("page_size") is not None:
            assert len(data["rows"]) <= int(params["page_size"])
        if "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
            assert len(str(int(data["earliest_available_time_ms"]))) == 13
        query = data["query"]
        if "dataset" in params and query.get("dataset") is not None:
            assert query["dataset"] == params["dataset"]
        if "dataset" not in params and query.get("dataset") is not None:
            assert query["dataset"] == "kline_data_future"
        if "symbols" in params and isinstance(params["symbols"], list):
            for row in data["rows"]:
                if "symbol" in row:
                    assert row["symbol"] in params["symbols"]
        timestamps = []
        requested_fields = params.get("fields")
        for row in data["rows"]:
            time_value = row.get("timestamp", row.get("time", row.get("funding_time")))
            if time_value is not None:
                millis = int(time_value)
                timestamps.append(millis)
                assert len(str(millis)) == 13
                if params.get("start_time_ms") is not None and params.get("end_time_ms") is not None and len(str(int(params["start_time_ms"]))) == 13:
                    assert params["start_time_ms"] <= millis < params["end_time_ms"]
            for numeric_field in ("open", "high", "low", "close", "volume", "funding_rate", "open_interest"):
                if numeric_field in row and row[numeric_field] is not None:
                    assert Decimal(str(row[numeric_field])) is not None
            if requested_fields:
                allowed_fields = set(requested_fields) | {"dataset", "quality_flag", "created_at", "updated_at"}
                assert set(row).issubset(allowed_fields)
            if params.get("quality_flags") and "quality_flag" in row:
                assert str(row["quality_flag"]).upper() in {flag.upper() for flag in params["quality_flags"]}


@allure.title('FD-TIME-NORMAL-001 - Normal - start_time_ms=1704067200000`，`end_time_ms=1704153600000')
@allure.feature('factor-data')
@allure.story('Normal')
@pytest.mark.factor_data_api
def test_fd_time_normal_001(factor_data_api):
    """
    Case ID: FD-TIME-NORMAL-001
    测试大类: factor-data
    测试类型: Normal
    测试目的: start_time_ms=1704067200000`，`end_time_ms=1704153600000
    预期断言: 成功；rows 时间落在 `[start,end)
    """

    params = {'dataset': 'kline_data_future', 'symbols': ['BTCUSDT'], 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'page_size': 100, 'sort': 'asc', 'include_symbol_coverage': False}

    response = factor_data_api.query(**params)

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
    assert "query" in data
    assert "rows" in data
    assert "next_cursor" in data
    assert "has_more" in data
    assert "row_count_returned" in data
    assert isinstance(data["rows"], list)
    assert isinstance(data["has_more"], bool)
    assert data["row_count_returned"] == len(data["rows"])
    if params.get("page_size") is not None:
        assert len(data["rows"]) <= int(params["page_size"])
    if "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    query = data["query"]
    if "dataset" in params and query.get("dataset") is not None:
        assert query["dataset"] == params["dataset"]
    if "dataset" not in params and query.get("dataset") is not None:
        assert query["dataset"] == "kline_data_future"
    if "symbols" in params and isinstance(params["symbols"], list):
        for row in data["rows"]:
            if "symbol" in row:
                assert row["symbol"] in params["symbols"]
    timestamps = []
    requested_fields = params.get("fields")
    for row in data["rows"]:
        time_value = row.get("timestamp", row.get("time", row.get("funding_time")))
        if time_value is not None:
            millis = int(time_value)
            timestamps.append(millis)
            assert len(str(millis)) == 13
            if params.get("start_time_ms") is not None and params.get("end_time_ms") is not None and len(str(int(params["start_time_ms"]))) == 13:
                assert params["start_time_ms"] <= millis < params["end_time_ms"]
        for numeric_field in ("open", "high", "low", "close", "volume", "funding_rate", "open_interest"):
            if numeric_field in row and row[numeric_field] is not None:
                assert Decimal(str(row[numeric_field])) is not None
        if requested_fields:
            allowed_fields = set(requested_fields) | {"dataset", "quality_flag", "created_at", "updated_at"}
            assert set(row).issubset(allowed_fields)
        if params.get("quality_flags") and "quality_flag" in row:
            assert str(row["quality_flag"]).upper() in {flag.upper() for flag in params["quality_flags"]}


@allure.title('FD-TIME-BOUNDARY-001 - Boundary - 很小时间窗，例如 1 分钟')
@allure.feature('factor-data')
@allure.story('Boundary')
@pytest.mark.factor_data_api
def test_fd_time_boundary_001(factor_data_api):
    """
    Case ID: FD-TIME-BOUNDARY-001
    测试大类: factor-data
    测试类型: Boundary
    测试目的: 很小时间窗，例如 1 分钟
    预期断言: 成功或空窗提示；不能 500
    """

    params = {'dataset': 'kline_data_future', 'symbols': ['BTCUSDT'], 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704067260000, 'page_size': 100, 'sort': 'asc', 'include_symbol_coverage': False}

    try:
        response = factor_data_api.query(**params)
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
        assert "query" in data
        assert "rows" in data
        assert "next_cursor" in data
        assert "has_more" in data
        assert "row_count_returned" in data
        assert isinstance(data["rows"], list)
        assert isinstance(data["has_more"], bool)
        assert data["row_count_returned"] == len(data["rows"])
        if params.get("page_size") is not None:
            assert len(data["rows"]) <= int(params["page_size"])
        if "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
            assert len(str(int(data["earliest_available_time_ms"]))) == 13
        query = data["query"]
        if "dataset" in params and query.get("dataset") is not None:
            assert query["dataset"] == params["dataset"]
        if "dataset" not in params and query.get("dataset") is not None:
            assert query["dataset"] == "kline_data_future"
        if "symbols" in params and isinstance(params["symbols"], list):
            for row in data["rows"]:
                if "symbol" in row:
                    assert row["symbol"] in params["symbols"]
        timestamps = []
        requested_fields = params.get("fields")
        for row in data["rows"]:
            time_value = row.get("timestamp", row.get("time", row.get("funding_time")))
            if time_value is not None:
                millis = int(time_value)
                timestamps.append(millis)
                assert len(str(millis)) == 13
                if params.get("start_time_ms") is not None and params.get("end_time_ms") is not None and len(str(int(params["start_time_ms"]))) == 13:
                    assert params["start_time_ms"] <= millis < params["end_time_ms"]
            for numeric_field in ("open", "high", "low", "close", "volume", "funding_rate", "open_interest"):
                if numeric_field in row and row[numeric_field] is not None:
                    assert Decimal(str(row[numeric_field])) is not None
            if requested_fields:
                allowed_fields = set(requested_fields) | {"dataset", "quality_flag", "created_at", "updated_at"}
                assert set(row).issubset(allowed_fields)
            if params.get("quality_flags") and "quality_flag" in row:
                assert str(row["quality_flag"]).upper() in {flag.upper() for flag in params["quality_flags"]}


@allure.title('FD-TIME-PARAM-001 - ParamError - end_time_ms == start_time_ms')
@allure.feature('factor-data')
@allure.story('ParamError')
@pytest.mark.factor_data_api
def test_fd_time_param_001(factor_data_api):
    """
    Case ID: FD-TIME-PARAM-001
    测试大类: factor-data
    测试类型: ParamError
    测试目的: end_time_ms == start_time_ms
    预期断言: 返回时间窗错误
    """

    params = {'dataset': 'kline_data_future', 'symbols': ['BTCUSDT'], 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704067200000, 'page_size': 100, 'sort': 'asc', 'include_symbol_coverage': False}

    try:
        response = factor_data_api.query(**params)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert any(key in str(body).lower() for key in ('dataset', 'symbols', 'time', 'page_size', 'sort', 'field', 'quality'))
    else:
        assert "code" in body
        assert "status" in body
        assert "message" in body
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] in {"error", "fail", "failed"}
    assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('FD-TIME-PARAM-002 - ParamError - end_time_ms < start_time_ms')
@allure.feature('factor-data')
@allure.story('ParamError')
@pytest.mark.factor_data_api
def test_fd_time_param_002(factor_data_api):
    """
    Case ID: FD-TIME-PARAM-002
    测试大类: factor-data
    测试类型: ParamError
    测试目的: end_time_ms < start_time_ms
    预期断言: 返回时间窗错误
    """

    params = {'dataset': 'kline_data_future', 'symbols': ['BTCUSDT'], 'interval': '1m', 'start_time_ms': 1704153600000, 'end_time_ms': 1704067200000, 'page_size': 100, 'sort': 'asc', 'include_symbol_coverage': False}

    try:
        response = factor_data_api.query(**params)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert any(key in str(body).lower() for key in ('dataset', 'symbols', 'time', 'page_size', 'sort', 'field', 'quality'))
    else:
        assert "code" in body
        assert "status" in body
        assert "message" in body
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] in {"error", "fail", "failed"}
    assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('FD-TIME-PARAM-003 - ParamError - 秒级时间戳 `1704067200')
@allure.feature('factor-data')
@allure.story('ParamError')
@pytest.mark.factor_data_api
def test_fd_time_param_003(factor_data_api):
    """
    Case ID: FD-TIME-PARAM-003
    测试大类: factor-data
    测试类型: ParamError
    测试目的: 秒级时间戳 `1704067200
    预期断言: 返回无数据提示或时间粒度错误；若返回 success，必须通过 coverage/earliest_available_time_ms 暴露无数据
    """

    params = {'dataset': 'kline_data_future', 'symbols': ['BTCUSDT'], 'interval': '1m', 'start_time_ms': 1704067200, 'end_time_ms': 1704153600, 'page_size': 100, 'sort': 'asc', 'include_symbol_coverage': False}

    try:
        response = factor_data_api.query(**params)
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
        assert "query" in data
        assert "rows" in data
        assert "next_cursor" in data
        assert "has_more" in data
        assert "row_count_returned" in data
        assert isinstance(data["rows"], list)
        assert isinstance(data["has_more"], bool)
        assert data["row_count_returned"] == len(data["rows"])
        if params.get("page_size") is not None:
            assert len(data["rows"]) <= int(params["page_size"])
        if "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
            assert len(str(int(data["earliest_available_time_ms"]))) == 13
        query = data["query"]
        if "dataset" in params and query.get("dataset") is not None:
            assert query["dataset"] == params["dataset"]
        if "dataset" not in params and query.get("dataset") is not None:
            assert query["dataset"] == "kline_data_future"
        if "symbols" in params and isinstance(params["symbols"], list):
            for row in data["rows"]:
                if "symbol" in row:
                    assert row["symbol"] in params["symbols"]
        timestamps = []
        requested_fields = params.get("fields")
        for row in data["rows"]:
            time_value = row.get("timestamp", row.get("time", row.get("funding_time")))
            if time_value is not None:
                millis = int(time_value)
                timestamps.append(millis)
                assert len(str(millis)) == 13
                if params.get("start_time_ms") is not None and params.get("end_time_ms") is not None and len(str(int(params["start_time_ms"]))) == 13:
                    assert params["start_time_ms"] <= millis < params["end_time_ms"]
            for numeric_field in ("open", "high", "low", "close", "volume", "funding_rate", "open_interest"):
                if numeric_field in row and row[numeric_field] is not None:
                    assert Decimal(str(row[numeric_field])) is not None
            if requested_fields:
                allowed_fields = set(requested_fields) | {"dataset", "quality_flag", "created_at", "updated_at"}
                assert set(row).issubset(allowed_fields)
            if params.get("quality_flags") and "quality_flag" in row:
                assert str(row["quality_flag"]).upper() in {flag.upper() for flag in params["quality_flags"]}


@allure.title('FD-FIELDS-NORMAL-001 - Normal - fields=["symbol","timestamp","close"]` 用于 K 线 dataset')
@allure.feature('factor-data')
@allure.story('Normal')
@pytest.mark.factor_data_api
def test_fd_fields_normal_001(factor_data_api):
    """
    Case ID: FD-FIELDS-NORMAL-001
    测试大类: factor-data
    测试类型: Normal
    测试目的: fields=["symbol","timestamp","close"]` 用于 K 线 dataset
    预期断言: rows 只返回请求字段和服务保留字段；不返回大量无关列
    """

    params = {'dataset': 'kline_data_future', 'symbols': ['BTCUSDT'], 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'fields': ['symbol', 'timestamp', 'close'], 'page_size': 100, 'sort': 'asc', 'include_symbol_coverage': False}

    response = factor_data_api.query(**params)

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
    assert "query" in data
    assert "rows" in data
    assert "next_cursor" in data
    assert "has_more" in data
    assert "row_count_returned" in data
    assert isinstance(data["rows"], list)
    assert isinstance(data["has_more"], bool)
    assert data["row_count_returned"] == len(data["rows"])
    if params.get("page_size") is not None:
        assert len(data["rows"]) <= int(params["page_size"])
    if "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    query = data["query"]
    if "dataset" in params and query.get("dataset") is not None:
        assert query["dataset"] == params["dataset"]
    if "dataset" not in params and query.get("dataset") is not None:
        assert query["dataset"] == "kline_data_future"
    if "symbols" in params and isinstance(params["symbols"], list):
        for row in data["rows"]:
            if "symbol" in row:
                assert row["symbol"] in params["symbols"]
    timestamps = []
    requested_fields = params.get("fields")
    for row in data["rows"]:
        time_value = row.get("timestamp", row.get("time", row.get("funding_time")))
        if time_value is not None:
            millis = int(time_value)
            timestamps.append(millis)
            assert len(str(millis)) == 13
            if params.get("start_time_ms") is not None and params.get("end_time_ms") is not None and len(str(int(params["start_time_ms"]))) == 13:
                assert params["start_time_ms"] <= millis < params["end_time_ms"]
        for numeric_field in ("open", "high", "low", "close", "volume", "funding_rate", "open_interest"):
            if numeric_field in row and row[numeric_field] is not None:
                assert Decimal(str(row[numeric_field])) is not None
        if requested_fields:
            allowed_fields = set(requested_fields) | {"dataset", "quality_flag", "created_at", "updated_at"}
            assert set(row).issubset(allowed_fields)
        if params.get("quality_flags") and "quality_flag" in row:
            assert str(row["quality_flag"]).upper() in {flag.upper() for flag in params["quality_flags"]}


@allure.title('FD-FIELDS-BOUNDARY-001 - Boundary - fields=[]` 或省略')
@allure.feature('factor-data')
@allure.story('Boundary')
@pytest.mark.factor_data_api
def test_fd_fields_boundary_001(factor_data_api):
    """
    Case ID: FD-FIELDS-BOUNDARY-001
    测试大类: factor-data
    测试类型: Boundary
    测试目的: fields=[]` 或省略
    预期断言: 使用 dataset 默认字段
    """

    params = {'dataset': 'kline_data_future', 'symbols': ['BTCUSDT'], 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'fields': [], 'page_size': 100, 'sort': 'asc', 'include_symbol_coverage': False}

    response = factor_data_api.query(**params)

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
    assert "query" in data
    assert "rows" in data
    assert "next_cursor" in data
    assert "has_more" in data
    assert "row_count_returned" in data
    assert isinstance(data["rows"], list)
    assert isinstance(data["has_more"], bool)
    assert data["row_count_returned"] == len(data["rows"])
    if params.get("page_size") is not None:
        assert len(data["rows"]) <= int(params["page_size"])
    if "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    query = data["query"]
    if "dataset" in params and query.get("dataset") is not None:
        assert query["dataset"] == params["dataset"]
    if "dataset" not in params and query.get("dataset") is not None:
        assert query["dataset"] == "kline_data_future"
    if "symbols" in params and isinstance(params["symbols"], list):
        for row in data["rows"]:
            if "symbol" in row:
                assert row["symbol"] in params["symbols"]
    timestamps = []
    requested_fields = params.get("fields")
    for row in data["rows"]:
        time_value = row.get("timestamp", row.get("time", row.get("funding_time")))
        if time_value is not None:
            millis = int(time_value)
            timestamps.append(millis)
            assert len(str(millis)) == 13
            if params.get("start_time_ms") is not None and params.get("end_time_ms") is not None and len(str(int(params["start_time_ms"]))) == 13:
                assert params["start_time_ms"] <= millis < params["end_time_ms"]
        for numeric_field in ("open", "high", "low", "close", "volume", "funding_rate", "open_interest"):
            if numeric_field in row and row[numeric_field] is not None:
                assert Decimal(str(row[numeric_field])) is not None
        if requested_fields:
            allowed_fields = set(requested_fields) | {"dataset", "quality_flag", "created_at", "updated_at"}
            assert set(row).issubset(allowed_fields)
        if params.get("quality_flags") and "quality_flag" in row:
            assert str(row["quality_flag"]).upper() in {flag.upper() for flag in params["quality_flags"]}


@allure.title('FD-FIELDS-PARAM-001 - ParamError - fields=["not_a_column"]')
@allure.feature('factor-data')
@allure.story('ParamError')
@pytest.mark.factor_data_api
def test_fd_fields_param_001(factor_data_api):
    """
    Case ID: FD-FIELDS-PARAM-001
    测试大类: factor-data
    测试类型: ParamError
    测试目的: fields=["not_a_column"]
    预期断言: 返回字段错误或明确业务错误；不能 500
    """

    params = {'dataset': 'kline_data_future', 'symbols': ['BTCUSDT'], 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'fields': ['not_a_column'], 'page_size': 100, 'sort': 'asc', 'include_symbol_coverage': False}

    try:
        response = factor_data_api.query(**params)
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
        assert "query" in data
        assert "rows" in data
        assert "next_cursor" in data
        assert "has_more" in data
        assert "row_count_returned" in data
        assert isinstance(data["rows"], list)
        assert isinstance(data["has_more"], bool)
        assert data["row_count_returned"] == len(data["rows"])
        if params.get("page_size") is not None:
            assert len(data["rows"]) <= int(params["page_size"])
        if "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
            assert len(str(int(data["earliest_available_time_ms"]))) == 13
        query = data["query"]
        if "dataset" in params and query.get("dataset") is not None:
            assert query["dataset"] == params["dataset"]
        if "dataset" not in params and query.get("dataset") is not None:
            assert query["dataset"] == "kline_data_future"
        if "symbols" in params and isinstance(params["symbols"], list):
            for row in data["rows"]:
                if "symbol" in row:
                    assert row["symbol"] in params["symbols"]
        timestamps = []
        requested_fields = params.get("fields")
        for row in data["rows"]:
            time_value = row.get("timestamp", row.get("time", row.get("funding_time")))
            if time_value is not None:
                millis = int(time_value)
                timestamps.append(millis)
                assert len(str(millis)) == 13
                if params.get("start_time_ms") is not None and params.get("end_time_ms") is not None and len(str(int(params["start_time_ms"]))) == 13:
                    assert params["start_time_ms"] <= millis < params["end_time_ms"]
            for numeric_field in ("open", "high", "low", "close", "volume", "funding_rate", "open_interest"):
                if numeric_field in row and row[numeric_field] is not None:
                    assert Decimal(str(row[numeric_field])) is not None
            if requested_fields:
                allowed_fields = set(requested_fields) | {"dataset", "quality_flag", "created_at", "updated_at"}
                assert set(row).issubset(allowed_fields)
            if params.get("quality_flags") and "quality_flag" in row:
                assert str(row["quality_flag"]).upper() in {flag.upper() for flag in params["quality_flags"]}


@allure.title('FD-QUALITY-NORMAL-001 - Normal - quality_flags=["OK"]')
@allure.feature('factor-data')
@allure.story('Normal')
@pytest.mark.factor_data_api
def test_fd_quality_normal_001(factor_data_api):
    """
    Case ID: FD-QUALITY-NORMAL-001
    测试大类: factor-data
    测试类型: Normal
    测试目的: quality_flags=["OK"]
    预期断言: 若 dataset 支持 quality_flag，rows 的质量标记符合请求
    """

    params = {'dataset': 'kline_data_future', 'symbols': ['BTCUSDT'], 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'quality_flags': ['OK'], 'page_size': 100, 'sort': 'asc', 'include_symbol_coverage': False}

    response = factor_data_api.query(**params)

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
    assert "query" in data
    assert "rows" in data
    assert "next_cursor" in data
    assert "has_more" in data
    assert "row_count_returned" in data
    assert isinstance(data["rows"], list)
    assert isinstance(data["has_more"], bool)
    assert data["row_count_returned"] == len(data["rows"])
    if params.get("page_size") is not None:
        assert len(data["rows"]) <= int(params["page_size"])
    if "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    query = data["query"]
    if "dataset" in params and query.get("dataset") is not None:
        assert query["dataset"] == params["dataset"]
    if "dataset" not in params and query.get("dataset") is not None:
        assert query["dataset"] == "kline_data_future"
    if "symbols" in params and isinstance(params["symbols"], list):
        for row in data["rows"]:
            if "symbol" in row:
                assert row["symbol"] in params["symbols"]
    timestamps = []
    requested_fields = params.get("fields")
    for row in data["rows"]:
        time_value = row.get("timestamp", row.get("time", row.get("funding_time")))
        if time_value is not None:
            millis = int(time_value)
            timestamps.append(millis)
            assert len(str(millis)) == 13
            if params.get("start_time_ms") is not None and params.get("end_time_ms") is not None and len(str(int(params["start_time_ms"]))) == 13:
                assert params["start_time_ms"] <= millis < params["end_time_ms"]
        for numeric_field in ("open", "high", "low", "close", "volume", "funding_rate", "open_interest"):
            if numeric_field in row and row[numeric_field] is not None:
                assert Decimal(str(row[numeric_field])) is not None
        if requested_fields:
            allowed_fields = set(requested_fields) | {"dataset", "quality_flag", "created_at", "updated_at"}
            assert set(row).issubset(allowed_fields)
        if params.get("quality_flags") and "quality_flag" in row:
            assert str(row["quality_flag"]).upper() in {flag.upper() for flag in params["quality_flags"]}


@allure.title('FD-QUALITY-BOUNDARY-001 - Boundary - quality_flags=["ok"]')
@allure.feature('factor-data')
@allure.story('Boundary')
@pytest.mark.factor_data_api
def test_fd_quality_boundary_001(factor_data_api):
    """
    Case ID: FD-QUALITY-BOUNDARY-001
    测试大类: factor-data
    测试类型: Boundary
    测试目的: quality_flags=["ok"]
    预期断言: 大小写不敏感或返回明确提示
    """

    params = {'dataset': 'kline_data_future', 'symbols': ['BTCUSDT'], 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'quality_flags': ['ok'], 'page_size': 100, 'sort': 'asc', 'include_symbol_coverage': False}

    try:
        response = factor_data_api.query(**params)
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
        assert "query" in data
        assert "rows" in data
        assert "next_cursor" in data
        assert "has_more" in data
        assert "row_count_returned" in data
        assert isinstance(data["rows"], list)
        assert isinstance(data["has_more"], bool)
        assert data["row_count_returned"] == len(data["rows"])
        if params.get("page_size") is not None:
            assert len(data["rows"]) <= int(params["page_size"])
        if "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
            assert len(str(int(data["earliest_available_time_ms"]))) == 13
        query = data["query"]
        if "dataset" in params and query.get("dataset") is not None:
            assert query["dataset"] == params["dataset"]
        if "dataset" not in params and query.get("dataset") is not None:
            assert query["dataset"] == "kline_data_future"
        if "symbols" in params and isinstance(params["symbols"], list):
            for row in data["rows"]:
                if "symbol" in row:
                    assert row["symbol"] in params["symbols"]
        timestamps = []
        requested_fields = params.get("fields")
        for row in data["rows"]:
            time_value = row.get("timestamp", row.get("time", row.get("funding_time")))
            if time_value is not None:
                millis = int(time_value)
                timestamps.append(millis)
                assert len(str(millis)) == 13
                if params.get("start_time_ms") is not None and params.get("end_time_ms") is not None and len(str(int(params["start_time_ms"]))) == 13:
                    assert params["start_time_ms"] <= millis < params["end_time_ms"]
            for numeric_field in ("open", "high", "low", "close", "volume", "funding_rate", "open_interest"):
                if numeric_field in row and row[numeric_field] is not None:
                    assert Decimal(str(row[numeric_field])) is not None
            if requested_fields:
                allowed_fields = set(requested_fields) | {"dataset", "quality_flag", "created_at", "updated_at"}
                assert set(row).issubset(allowed_fields)
            if params.get("quality_flags") and "quality_flag" in row:
                assert str(row["quality_flag"]).upper() in {flag.upper() for flag in params["quality_flags"]}


@allure.title('FD-QUALITY-PARAM-001 - ParamError - 对不含 quality_flag 的 dataset 使用 `quality_flags=["OK"]')
@allure.feature('factor-data')
@allure.story('ParamError')
@pytest.mark.factor_data_api
def test_fd_quality_param_001(factor_data_api):
    """
    Case ID: FD-QUALITY-PARAM-001
    测试大类: factor-data
    测试类型: ParamError
    测试目的: 对不含 quality_flag 的 dataset 使用 `quality_flags=["OK"]
    预期断言: 返回明确错误、忽略过滤或空结果；需按实际行为固化，不能 500
    """

    params = {'dataset': 'binance_usdm_funding_rate_clean', 'symbols': ['BTCUSDT'], 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'quality_flags': ['OK'], 'page_size': 100, 'sort': 'asc', 'include_symbol_coverage': False}

    try:
        response = factor_data_api.query(**params)
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
        assert "query" in data
        assert "rows" in data
        assert "next_cursor" in data
        assert "has_more" in data
        assert "row_count_returned" in data
        assert isinstance(data["rows"], list)
        assert isinstance(data["has_more"], bool)
        assert data["row_count_returned"] == len(data["rows"])
        if params.get("page_size") is not None:
            assert len(data["rows"]) <= int(params["page_size"])
        if "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
            assert len(str(int(data["earliest_available_time_ms"]))) == 13
        query = data["query"]
        if "dataset" in params and query.get("dataset") is not None:
            assert query["dataset"] == params["dataset"]
        if "dataset" not in params and query.get("dataset") is not None:
            assert query["dataset"] == "kline_data_future"
        if "symbols" in params and isinstance(params["symbols"], list):
            for row in data["rows"]:
                if "symbol" in row:
                    assert row["symbol"] in params["symbols"]
        timestamps = []
        requested_fields = params.get("fields")
        for row in data["rows"]:
            time_value = row.get("timestamp", row.get("time", row.get("funding_time")))
            if time_value is not None:
                millis = int(time_value)
                timestamps.append(millis)
                assert len(str(millis)) == 13
                if params.get("start_time_ms") is not None and params.get("end_time_ms") is not None and len(str(int(params["start_time_ms"]))) == 13:
                    assert params["start_time_ms"] <= millis < params["end_time_ms"]
            for numeric_field in ("open", "high", "low", "close", "volume", "funding_rate", "open_interest"):
                if numeric_field in row and row[numeric_field] is not None:
                    assert Decimal(str(row[numeric_field])) is not None
            if requested_fields:
                allowed_fields = set(requested_fields) | {"dataset", "quality_flag", "created_at", "updated_at"}
                assert set(row).issubset(allowed_fields)
            if params.get("quality_flags") and "quality_flag" in row:
                assert str(row["quality_flag"]).upper() in {flag.upper() for flag in params["quality_flags"]}


@allure.title('FD-PAGE-BOUNDARY-001 - Boundary - page_size=1')
@allure.feature('factor-data')
@allure.story('Boundary')
@pytest.mark.factor_data_api
def test_fd_page_boundary_001(factor_data_api):
    """
    Case ID: FD-PAGE-BOUNDARY-001
    测试大类: factor-data
    测试类型: Boundary
    测试目的: page_size=1
    预期断言: rows 长度不超过 1；`row_count_returned == len(rows)
    """

    params = {'dataset': 'kline_data_future', 'symbols': ['BTCUSDT'], 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'page_size': 1, 'sort': 'asc', 'include_symbol_coverage': False}

    response = factor_data_api.query(**params)

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
    assert "query" in data
    assert "rows" in data
    assert "next_cursor" in data
    assert "has_more" in data
    assert "row_count_returned" in data
    assert isinstance(data["rows"], list)
    assert isinstance(data["has_more"], bool)
    assert data["row_count_returned"] == len(data["rows"])
    if params.get("page_size") is not None:
        assert len(data["rows"]) <= int(params["page_size"])
    if "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    query = data["query"]
    if "dataset" in params and query.get("dataset") is not None:
        assert query["dataset"] == params["dataset"]
    if "dataset" not in params and query.get("dataset") is not None:
        assert query["dataset"] == "kline_data_future"
    if "symbols" in params and isinstance(params["symbols"], list):
        for row in data["rows"]:
            if "symbol" in row:
                assert row["symbol"] in params["symbols"]
    timestamps = []
    requested_fields = params.get("fields")
    for row in data["rows"]:
        time_value = row.get("timestamp", row.get("time", row.get("funding_time")))
        if time_value is not None:
            millis = int(time_value)
            timestamps.append(millis)
            assert len(str(millis)) == 13
            if params.get("start_time_ms") is not None and params.get("end_time_ms") is not None and len(str(int(params["start_time_ms"]))) == 13:
                assert params["start_time_ms"] <= millis < params["end_time_ms"]
        for numeric_field in ("open", "high", "low", "close", "volume", "funding_rate", "open_interest"):
            if numeric_field in row and row[numeric_field] is not None:
                assert Decimal(str(row[numeric_field])) is not None
        if requested_fields:
            allowed_fields = set(requested_fields) | {"dataset", "quality_flag", "created_at", "updated_at"}
            assert set(row).issubset(allowed_fields)
        if params.get("quality_flags") and "quality_flag" in row:
            assert str(row["quality_flag"]).upper() in {flag.upper() for flag in params["quality_flags"]}


@allure.title('FD-PAGE-BOUNDARY-002 - Boundary - page_size=5000')
@allure.feature('factor-data')
@allure.story('Boundary')
@pytest.mark.factor_data_api
def test_fd_page_boundary_002(factor_data_api):
    """
    Case ID: FD-PAGE-BOUNDARY-002
    测试大类: factor-data
    测试类型: Boundary
    测试目的: page_size=5000
    预期断言: 不超过 5000；响应不 500
    """

    params = {'dataset': 'kline_data_future', 'symbols': ['BTCUSDT'], 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'page_size': 5000, 'sort': 'asc', 'include_symbol_coverage': False}

    try:
        response = factor_data_api.query(**params)
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
        assert "query" in data
        assert "rows" in data
        assert "next_cursor" in data
        assert "has_more" in data
        assert "row_count_returned" in data
        assert isinstance(data["rows"], list)
        assert isinstance(data["has_more"], bool)
        assert data["row_count_returned"] == len(data["rows"])
        if params.get("page_size") is not None:
            assert len(data["rows"]) <= int(params["page_size"])
        if "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
            assert len(str(int(data["earliest_available_time_ms"]))) == 13
        query = data["query"]
        if "dataset" in params and query.get("dataset") is not None:
            assert query["dataset"] == params["dataset"]
        if "dataset" not in params and query.get("dataset") is not None:
            assert query["dataset"] == "kline_data_future"
        if "symbols" in params and isinstance(params["symbols"], list):
            for row in data["rows"]:
                if "symbol" in row:
                    assert row["symbol"] in params["symbols"]
        timestamps = []
        requested_fields = params.get("fields")
        for row in data["rows"]:
            time_value = row.get("timestamp", row.get("time", row.get("funding_time")))
            if time_value is not None:
                millis = int(time_value)
                timestamps.append(millis)
                assert len(str(millis)) == 13
                if params.get("start_time_ms") is not None and params.get("end_time_ms") is not None and len(str(int(params["start_time_ms"]))) == 13:
                    assert params["start_time_ms"] <= millis < params["end_time_ms"]
            for numeric_field in ("open", "high", "low", "close", "volume", "funding_rate", "open_interest"):
                if numeric_field in row and row[numeric_field] is not None:
                    assert Decimal(str(row[numeric_field])) is not None
            if requested_fields:
                allowed_fields = set(requested_fields) | {"dataset", "quality_flag", "created_at", "updated_at"}
                assert set(row).issubset(allowed_fields)
            if params.get("quality_flags") and "quality_flag" in row:
                assert str(row["quality_flag"]).upper() in {flag.upper() for flag in params["quality_flags"]}


@allure.title('FD-PAGE-PARAM-001 - ParamError - page_size=0')
@allure.feature('factor-data')
@allure.story('ParamError')
@pytest.mark.factor_data_api
def test_fd_page_param_001(factor_data_api):
    """
    Case ID: FD-PAGE-PARAM-001
    测试大类: factor-data
    测试类型: ParamError
    测试目的: page_size=0
    预期断言: 返回参数错误
    """

    params = {'dataset': 'kline_data_future', 'symbols': ['BTCUSDT'], 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'page_size': 0, 'sort': 'asc', 'include_symbol_coverage': False}

    try:
        response = factor_data_api.query(**params)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert any(key in str(body).lower() for key in ('dataset', 'symbols', 'time', 'page_size', 'sort', 'field', 'quality'))
    else:
        assert "code" in body
        assert "status" in body
        assert "message" in body
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] in {"error", "fail", "failed"}
    assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('FD-PAGE-PARAM-002 - ParamError - page_size=5001')
@allure.feature('factor-data')
@allure.story('ParamError')
@pytest.mark.factor_data_api
def test_fd_page_param_002(factor_data_api):
    """
    Case ID: FD-PAGE-PARAM-002
    测试大类: factor-data
    测试类型: ParamError
    测试目的: page_size=5001
    预期断言: 返回参数错误
    """

    params = {'dataset': 'kline_data_future', 'symbols': ['BTCUSDT'], 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'page_size': 5001, 'sort': 'asc', 'include_symbol_coverage': False}

    try:
        response = factor_data_api.query(**params)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert any(key in str(body).lower() for key in ('dataset', 'symbols', 'time', 'page_size', 'sort', 'field', 'quality'))
    else:
        assert "code" in body
        assert "status" in body
        assert "message" in body
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] in {"error", "fail", "failed"}
    assert not (str(body.get("code")) == "200" and body.get("status") == "success")


@allure.title('FD-CURSOR-NORMAL-001 - Normal - 首查 `page_size=1`，若 `has_more=true` 用 `next_cursor` 查第二页')
@allure.feature('factor-data')
@allure.story('Normal')
@pytest.mark.factor_data_api
def test_fd_cursor_normal_001(factor_data_api):
    """
    Case ID: FD-CURSOR-NORMAL-001
    测试大类: factor-data
    测试类型: Normal
    测试目的: 首查 `page_size=1`，若 `has_more=true` 用 `next_cursor` 查第二页
    预期断言: 第二页 rows 与第一页无重复；cursor 请求时 coverage 可为空或不重复计算
    """

    params = {'dataset': 'kline_data_future', 'symbols': ['BTCUSDT'], 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'page_size': 1, 'sort': 'asc', 'include_symbol_coverage': False}

    response = factor_data_api.query(**params)

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
    assert "query" in data
    assert "rows" in data
    assert "next_cursor" in data
    assert "has_more" in data
    assert "row_count_returned" in data
    assert isinstance(data["rows"], list)
    assert isinstance(data["has_more"], bool)
    assert data["row_count_returned"] == len(data["rows"])
    if params.get("page_size") is not None:
        assert len(data["rows"]) <= int(params["page_size"])
    if "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    query = data["query"]
    if "dataset" in params and query.get("dataset") is not None:
        assert query["dataset"] == params["dataset"]
    if "dataset" not in params and query.get("dataset") is not None:
        assert query["dataset"] == "kline_data_future"
    if "symbols" in params and isinstance(params["symbols"], list):
        for row in data["rows"]:
            if "symbol" in row:
                assert row["symbol"] in params["symbols"]
    timestamps = []
    requested_fields = params.get("fields")
    for row in data["rows"]:
        time_value = row.get("timestamp", row.get("time", row.get("funding_time")))
        if time_value is not None:
            millis = int(time_value)
            timestamps.append(millis)
            assert len(str(millis)) == 13
            if params.get("start_time_ms") is not None and params.get("end_time_ms") is not None and len(str(int(params["start_time_ms"]))) == 13:
                assert params["start_time_ms"] <= millis < params["end_time_ms"]
        for numeric_field in ("open", "high", "low", "close", "volume", "funding_rate", "open_interest"):
            if numeric_field in row and row[numeric_field] is not None:
                assert Decimal(str(row[numeric_field])) is not None
        if requested_fields:
            allowed_fields = set(requested_fields) | {"dataset", "quality_flag", "created_at", "updated_at"}
            assert set(row).issubset(allowed_fields)
        if params.get("quality_flags") and "quality_flag" in row:
            assert str(row["quality_flag"]).upper() in {flag.upper() for flag in params["quality_flags"]}

    if data["has_more"] and data["next_cursor"]:
        second_params = dict(params)
        second_params["cursor"] = data["next_cursor"]
        second_response = factor_data_api.query(**second_params)
        assert second_response.status_code == 200
        second_body = second_response.json()
        assert "code" in second_body
        assert "status" in second_body
        assert "message" in second_body
        assert "data" in second_body
        assert str(second_body["code"]) == "200"
        assert second_body["status"] == "success"
        assert second_body["message"]
        assert "rows" in second_body["data"]
        first_rows = {str(row) for row in data["rows"]}
        second_rows = {str(row) for row in second_body["data"]["rows"]}
        assert first_rows.isdisjoint(second_rows)


@allure.title('FD-CURSOR-PARAM-001 - ParamError - cursor=invalid_cursor')
@allure.feature('factor-data')
@allure.story('ParamError')
@pytest.mark.factor_data_api
def test_fd_cursor_param_001(factor_data_api):
    """
    Case ID: FD-CURSOR-PARAM-001
    测试大类: factor-data
    测试类型: ParamError
    测试目的: cursor=invalid_cursor
    预期断言: 返回游标错误或业务错误；不能 500
    """

    params = {'dataset': 'kline_data_future', 'symbols': ['BTCUSDT'], 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'page_size': 100, 'cursor': 'invalid_cursor', 'sort': 'asc', 'include_symbol_coverage': False}

    try:
        response = factor_data_api.query(**params)
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
        assert "query" in data
        assert "rows" in data
        assert "next_cursor" in data
        assert "has_more" in data
        assert "row_count_returned" in data
        assert isinstance(data["rows"], list)
        assert isinstance(data["has_more"], bool)
        assert data["row_count_returned"] == len(data["rows"])
        if params.get("page_size") is not None:
            assert len(data["rows"]) <= int(params["page_size"])
        if "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
            assert len(str(int(data["earliest_available_time_ms"]))) == 13
        query = data["query"]
        if "dataset" in params and query.get("dataset") is not None:
            assert query["dataset"] == params["dataset"]
        if "dataset" not in params and query.get("dataset") is not None:
            assert query["dataset"] == "kline_data_future"
        if "symbols" in params and isinstance(params["symbols"], list):
            for row in data["rows"]:
                if "symbol" in row:
                    assert row["symbol"] in params["symbols"]
        timestamps = []
        requested_fields = params.get("fields")
        for row in data["rows"]:
            time_value = row.get("timestamp", row.get("time", row.get("funding_time")))
            if time_value is not None:
                millis = int(time_value)
                timestamps.append(millis)
                assert len(str(millis)) == 13
                if params.get("start_time_ms") is not None and params.get("end_time_ms") is not None and len(str(int(params["start_time_ms"]))) == 13:
                    assert params["start_time_ms"] <= millis < params["end_time_ms"]
            for numeric_field in ("open", "high", "low", "close", "volume", "funding_rate", "open_interest"):
                if numeric_field in row and row[numeric_field] is not None:
                    assert Decimal(str(row[numeric_field])) is not None
            if requested_fields:
                allowed_fields = set(requested_fields) | {"dataset", "quality_flag", "created_at", "updated_at"}
                assert set(row).issubset(allowed_fields)
            if params.get("quality_flags") and "quality_flag" in row:
                assert str(row["quality_flag"]).upper() in {flag.upper() for flag in params["quality_flags"]}


@allure.title('FD-SORT-NORMAL-001 - Normal - sort=asc')
@allure.feature('factor-data')
@allure.story('Normal')
@pytest.mark.factor_data_api
def test_fd_sort_normal_001(factor_data_api):
    """
    Case ID: FD-SORT-NORMAL-001
    测试大类: factor-data
    测试类型: Normal
    测试目的: sort=asc
    预期断言: rows 按业务时间列与 symbol 联合升序
    """

    params = {'dataset': 'kline_data_future', 'symbols': ['BTCUSDT'], 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'page_size': 100, 'sort': 'asc', 'include_symbol_coverage': False}

    response = factor_data_api.query(**params)

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
    assert "query" in data
    assert "rows" in data
    assert "next_cursor" in data
    assert "has_more" in data
    assert "row_count_returned" in data
    assert isinstance(data["rows"], list)
    assert isinstance(data["has_more"], bool)
    assert data["row_count_returned"] == len(data["rows"])
    if params.get("page_size") is not None:
        assert len(data["rows"]) <= int(params["page_size"])
    if "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    query = data["query"]
    if "dataset" in params and query.get("dataset") is not None:
        assert query["dataset"] == params["dataset"]
    if "dataset" not in params and query.get("dataset") is not None:
        assert query["dataset"] == "kline_data_future"
    if "symbols" in params and isinstance(params["symbols"], list):
        for row in data["rows"]:
            if "symbol" in row:
                assert row["symbol"] in params["symbols"]
    timestamps = []
    requested_fields = params.get("fields")
    for row in data["rows"]:
        time_value = row.get("timestamp", row.get("time", row.get("funding_time")))
        if time_value is not None:
            millis = int(time_value)
            timestamps.append(millis)
            assert len(str(millis)) == 13
            if params.get("start_time_ms") is not None and params.get("end_time_ms") is not None and len(str(int(params["start_time_ms"]))) == 13:
                assert params["start_time_ms"] <= millis < params["end_time_ms"]
        for numeric_field in ("open", "high", "low", "close", "volume", "funding_rate", "open_interest"):
            if numeric_field in row and row[numeric_field] is not None:
                assert Decimal(str(row[numeric_field])) is not None
        if requested_fields:
            allowed_fields = set(requested_fields) | {"dataset", "quality_flag", "created_at", "updated_at"}
            assert set(row).issubset(allowed_fields)
        if params.get("quality_flags") and "quality_flag" in row:
            assert str(row["quality_flag"]).upper() in {flag.upper() for flag in params["quality_flags"]}
    if len(timestamps) > 1:
        assert timestamps == sorted(timestamps)


@allure.title('FD-SORT-NORMAL-002 - Normal - sort=desc')
@allure.feature('factor-data')
@allure.story('Normal')
@pytest.mark.factor_data_api
def test_fd_sort_normal_002(factor_data_api):
    """
    Case ID: FD-SORT-NORMAL-002
    测试大类: factor-data
    测试类型: Normal
    测试目的: sort=desc
    预期断言: rows 按业务时间列与 symbol 联合降序
    """

    params = {'dataset': 'kline_data_future', 'symbols': ['BTCUSDT'], 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'page_size': 100, 'sort': 'desc', 'include_symbol_coverage': False}

    response = factor_data_api.query(**params)

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
    assert "query" in data
    assert "rows" in data
    assert "next_cursor" in data
    assert "has_more" in data
    assert "row_count_returned" in data
    assert isinstance(data["rows"], list)
    assert isinstance(data["has_more"], bool)
    assert data["row_count_returned"] == len(data["rows"])
    if params.get("page_size") is not None:
        assert len(data["rows"]) <= int(params["page_size"])
    if "earliest_available_time_ms" in data and data["earliest_available_time_ms"] is not None:
        assert len(str(int(data["earliest_available_time_ms"]))) == 13
    query = data["query"]
    if "dataset" in params and query.get("dataset") is not None:
        assert query["dataset"] == params["dataset"]
    if "dataset" not in params and query.get("dataset") is not None:
        assert query["dataset"] == "kline_data_future"
    if "symbols" in params and isinstance(params["symbols"], list):
        for row in data["rows"]:
            if "symbol" in row:
                assert row["symbol"] in params["symbols"]
    timestamps = []
    requested_fields = params.get("fields")
    for row in data["rows"]:
        time_value = row.get("timestamp", row.get("time", row.get("funding_time")))
        if time_value is not None:
            millis = int(time_value)
            timestamps.append(millis)
            assert len(str(millis)) == 13
            if params.get("start_time_ms") is not None and params.get("end_time_ms") is not None and len(str(int(params["start_time_ms"]))) == 13:
                assert params["start_time_ms"] <= millis < params["end_time_ms"]
        for numeric_field in ("open", "high", "low", "close", "volume", "funding_rate", "open_interest"):
            if numeric_field in row and row[numeric_field] is not None:
                assert Decimal(str(row[numeric_field])) is not None
        if requested_fields:
            allowed_fields = set(requested_fields) | {"dataset", "quality_flag", "created_at", "updated_at"}
            assert set(row).issubset(allowed_fields)
        if params.get("quality_flags") and "quality_flag" in row:
            assert str(row["quality_flag"]).upper() in {flag.upper() for flag in params["quality_flags"]}
    if len(timestamps) > 1:
        assert timestamps == sorted(timestamps, reverse=True)


@allure.title('FD-SORT-PARAM-001 - ParamError - sort=bad_sort')
@allure.feature('factor-data')
@allure.story('ParamError')
@pytest.mark.factor_data_api
def test_fd_sort_param_001(factor_data_api):
    """
    Case ID: FD-SORT-PARAM-001
    测试大类: factor-data
    测试类型: ParamError
    测试目的: sort=bad_sort
    预期断言: 返回枚举参数错误
    """

    params = {'dataset': 'kline_data_future', 'symbols': ['BTCUSDT'], 'interval': '1m', 'start_time_ms': 1704067200000, 'end_time_ms': 1704153600000, 'page_size': 100, 'sort': 'bad_sort', 'include_symbol_coverage': False}

    try:
        response = factor_data_api.query(**params)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500
    body = response.json() if response.content else {}
    assert str(body.get("code", "")) != "500"
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert any(key in str(body).lower() for key in ('dataset', 'symbols', 'time', 'page_size', 'sort', 'field', 'quality'))
    else:
        assert "code" in body
        assert "status" in body
        assert "message" in body
        assert str(body["code"]) in {"400", "422"}
        assert body["status"] in {"error", "fail", "failed"}
    assert not (str(body.get("code")) == "200" and body.get("status") == "success")
