# Kline API Test Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring `tests/test_kline_api.py` into full coverage alignment with `docs/test_cases_kline_data.md` for the 7 Kline Data legacy API endpoints.

**Architecture:** Keep all Kline Data live API tests in the existing `tests/test_kline_api.py` file. Add focused assertion helpers near the top of the file, then refactor or add tests so every `KD-*` case in the document maps to an automated test with matching Case ID and meaningful assertions. Do not change `api_services/kline_data_api.py` unless a parameter required by the document is impossible to send through the existing wrapper.

**Tech Stack:** Python 3.12, pytest, allure-pytest, requests, existing `KlineDataAPI`, existing `pytest.ini` markers `kline_api`, `dqc`, `logic`, and `performance`.

---

## Scope Check

This is one subsystem: Kline Data API tests. The implementation should not touch production API wrappers, service clients, settings, Jenkins, or documentation outside this plan unless a test cannot express a documented request through the existing `KlineDataAPI` methods.

The current test file has broad smoke coverage, but it does not fully assert the documented contracts. The implementation target is:

- Every documented `KD-*` Case ID appears in test code or in a parametrized case value.
- Existing non-documented performance tests may stay, but documented Case IDs must not be hidden behind old `Kline*_*` names.
- Success tests validate response envelope and relevant `data` shape.
- Raw and curated Kline tests validate pagination, item schema, timestamp, numeric fields, OHLC, filter echo, and time-window behavior where the document requires it.
- ParamError tests validate HTTP `400/422` or business code `400/422`, and they must not accept a success response with real data.

## File Structure

- Modify: `tests/test_kline_api.py`
  - Add constants matching the test-case document.
  - Add assertion helpers for success envelopes, error envelopes, no-500 checks, time-range shape, pagination shape, timestamp validation, numeric validation, OHLC, ordering, time windows, and quality flag matching.
  - Rename existing Allure titles and docstring Case IDs from `KlineFetch_Normal_001` style to `KD-FETCH-NORMAL-001` style.
  - Strengthen existing tests whose Case IDs already exist in the document.
  - Add missing documented cases using explicit test functions or compact parametrized tests with `allure.dynamic.title`.

- Read only: `docs/test_cases_kline_data.md`
  - Use as the source of truth for Case IDs and expected assertions.

- Read only: `api_services/kline_data_api.py`
  - Existing methods already cover the 7 endpoints and parameters required by the document.

## Task 1: Add Shared Test Constants And Assertion Helpers

**Files:**
- Modify: `tests/test_kline_api.py`

- [ ] **Step 1: Add imports and constants**

Add this near the existing imports and constants:

```python
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

LIMIT_SMALL = 1
LIMIT_NORMAL = 10
LIMIT_MAX = 200000
LIMIT_TOO_LARGE = LIMIT_MAX + 1
INVALID_SYMBOL = "NOT_A_SYMBOL"
INVALID_INTERVAL = "99m"
```

- [ ] **Step 2: Add HTTP/error helper functions**

Add these below the constants:

```python
def _request_allowing_http_error(call: Callable[..., Any], **kwargs):
    try:
        return call(**kwargs)
    except HTTPError as exc:
        assert exc.response is not None
        return exc.response


def _json_body(response):
    return response.json() if response.content else {}


def _assert_success_envelope(response):
    assert response.status_code == 200
    body = response.json()
    assert "code" in body
    assert "status" in body
    assert "message" in body
    assert "data" in body
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert body["message"]
    return body


def _assert_not_server_error(response):
    assert response.status_code < 500
    body = _json_body(response)
    assert str(body.get("code", "")) != "500"
    return body


def _assert_error_response(response, expected_text: str):
    body = _assert_not_server_error(response)
    if response.status_code >= 400:
        assert response.status_code in {400, 422}
        assert expected_text.lower() in str(body).lower()
        return body

    assert str(body.get("code")) in {"400", "422"}
    assert body.get("status") == "error"
    assert expected_text.lower() in str(body.get("message", body)).lower()
    assert not (str(body.get("code")) == "200" and body.get("status") == "success")
    return body


def _assert_business_error_or_empty_data(response, expected_text: str | None = None):
    body = _assert_not_server_error(response)
    if response.status_code >= 400 or str(body.get("code")) in {"400", "422"}:
        if expected_text is not None:
            assert expected_text.lower() in str(body).lower()
        return body

    assert str(body.get("code")) == "200"
    assert body.get("status") == "success"
    data = body.get("data")
    assert data in (None, [], {}) or data.get("items", []) == []
    return body
```

- [ ] **Step 3: Add data-shape helper functions**

Add these below the HTTP helpers:

```python
def _assert_millis(value):
    assert value is not None
    millis = int(value)
    assert len(str(millis)) == 13
    return millis


def _assert_optional_millis(value):
    if value is None:
        return None
    return _assert_millis(value)


def _as_decimal(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise AssertionError(f"{value!r} is not numeric") from exc


def _assert_ohlc(item):
    open_price = _as_decimal(item["open"])
    high = _as_decimal(item["high"])
    low = _as_decimal(item["low"])
    close = _as_decimal(item["close"])
    assert high >= open_price
    assert high >= close
    assert high >= low
    assert low <= open_price
    assert low <= close
    assert low <= high


def _assert_time_range_data(body):
    data = body["data"]
    assert isinstance(data, dict)
    assert "filters" in data
    for section_name in ("raw", "curated"):
        section = data[section_name]
        assert "time_field" in section
        assert "min_time_ms" in section
        assert "max_time_ms" in section
        assert "has_data" in section
        min_time = _assert_optional_millis(section["min_time_ms"])
        max_time = _assert_optional_millis(section["max_time_ms"])
        if section["has_data"]:
            assert min_time is not None
            assert max_time is not None
            assert min_time <= max_time


def _assert_page_data(body, *, limit=None, offset=None, include_total=None):
    data = body["data"]
    assert isinstance(data, dict)
    assert "filters" in data
    assert "pagination" in data
    assert "items" in data
    assert isinstance(data["items"], list)
    pagination = data["pagination"]
    if limit is not None:
        assert len(data["items"]) <= limit
        assert pagination["limit"] == limit
    if offset is not None:
        assert pagination["offset"] == offset
    if include_total is not None:
        assert pagination["include_total"] is include_total
    if "total" in pagination and pagination["total"] is not None:
        assert isinstance(pagination["total"], int)
        assert pagination["total"] >= 0
    return data["items"]


def _assert_kline_items_schema(items, *, symbol=SYMBOL, interval=INTERVAL, curated=False):
    for item in items:
        for field in ("symbol", "timestamp", "interval", "open", "high", "low", "close", "volume"):
            assert field in item
        assert item["symbol"] == symbol
        assert item["interval"] == interval
        _assert_millis(item["timestamp"])
        if "close_time" in item and item["close_time"] is not None:
            _assert_millis(item["close_time"])
        for numeric_field in ("open", "high", "low", "close", "volume", "quote_volume"):
            if numeric_field in item and item[numeric_field] is not None:
                _as_decimal(item[numeric_field])
        _assert_ohlc(item)
        if curated:
            assert "quality_flag" in item or "repair_tag" in item


def _assert_items_sorted_by_timestamp(items):
    timestamps = [_assert_millis(item["timestamp"]) for item in items]
    assert timestamps == sorted(timestamps)


def _assert_items_within_window(items, *, start_time_ms=START_TIME_MS, end_time_ms=END_TIME_MS):
    for item in items:
        timestamp = _assert_millis(item["timestamp"])
        assert start_time_ms <= timestamp < end_time_ms


def _assert_quality_flag_matches(items, expected_flag):
    for item in items:
        if item.get("quality_flag") is not None:
            assert str(item["quality_flag"]).upper() == expected_flag.upper()
```

- [ ] **Step 4: Verify helper syntax**

Run:

```bash
pytest --collect-only -q tests/test_kline_api.py
```

Expected:

```text
collection succeeds without SyntaxError or import errors
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_kline_api.py
git commit -m "test: add kline api assertion helpers"
```

## Task 2: Align Existing Case IDs And Strengthen Existing Documented Tests

**Files:**
- Modify: `tests/test_kline_api.py`

- [ ] **Step 1: Rename existing Case IDs to documented `KD-*` IDs**

Replace the old Case IDs in Allure titles and docstrings:

```text
KlineFetch_Normal_001 -> KD-FETCH-NORMAL-001
KlineFetch_ParamError_001 -> KD-FETCH-PARAM-001
KlineFetch_Boundary_001 -> KD-FETCH-BOUNDARY-001
KlineFetch_Response_001 -> KD-FETCH-RESPONSE-001
KlineFetch_Performance_001 -> KD-FETCH-PERF-001
KlineUsdmTimeRange_Normal_001 -> KD-USDM-TIMERANGE-NORMAL-001
KlineUsdmTimeRange_ParamError_001 -> KD-USDM-TIMERANGE-PARAM-001
KlineUsdmTimeRange_Boundary_001 -> KD-USDM-TIMERANGE-BOUNDARY-001
KlineUsdmTimeRange_Response_001 -> KD-USDM-TIMERANGE-RESPONSE-001
KlineUsdmTimeRange_Performance_001 -> KD-USDM-TIMERANGE-PERF-001
KlineUsdmRaw_Normal_001 -> KD-USDM-RAW-NORMAL-001
KlineUsdmRaw_ParamError_001 -> KD-USDM-RAW-PARAM-001
KlineUsdmRaw_Response_001 -> KD-USDM-RAW-RESPONSE-001
KlineUsdmRaw_Performance_001 -> KD-USDM-RAW-PERF-001
KlineUsdmCurated_Normal_001 -> KD-USDM-CURATED-NORMAL-001
KlineUsdmCurated_ParamError_001 -> KD-USDM-CURATED-PARAM-001
KlineUsdmCurated_Response_001 -> KD-USDM-CURATED-RESPONSE-001
KlineSpotTimeRange_Normal_001 -> KD-SPOT-TIMERANGE-NORMAL-001
KlineSpotTimeRange_ParamError_001 -> KD-SPOT-TIMERANGE-PARAM-001
KlineSpotTimeRange_Boundary_001 -> KD-SPOT-TIMERANGE-BOUNDARY-001
KlineSpotRaw_Normal_001 -> KD-SPOT-RAW-NORMAL-001
KlineSpotRaw_ParamError_001 -> KD-SPOT-RAW-PARAM-001
KlineSpotRaw_Response_001 -> KD-SPOT-RAW-RESPONSE-001
KlineSpotCurated_Normal_001 -> KD-SPOT-CURATED-NORMAL-001
KlineSpotCurated_ParamError_001 -> KD-SPOT-CURATED-PARAM-001
KlineSpotCurated_Response_001 -> KD-SPOT-CURATED-RESPONSE-001
```

The existing Spot and curated performance tests are extra coverage not listed in the document. Keep them with descriptive names, but do not count them as document coverage.

- [ ] **Step 2: Replace repeated envelope assertions in existing success tests**

For each existing success test, replace the repeated block:

```python
assert response.status_code == 200
body = response.json()
assert body["code"] == "200"
assert body["status"] == "success"
assert body["message"]
assert "data" in body
```

with:

```python
body = _assert_success_envelope(response)
```

- [ ] **Step 3: Strengthen existing time-range tests**

For `KD-USDM-TIMERANGE-NORMAL-001`, `KD-USDM-TIMERANGE-BOUNDARY-001`, `KD-SPOT-TIMERANGE-NORMAL-001`, and `KD-SPOT-TIMERANGE-BOUNDARY-001`, add:

```python
body = _assert_success_envelope(response)
_assert_time_range_data(body)
```

- [ ] **Step 4: Strengthen existing raw page tests**

For `KD-USDM-RAW-NORMAL-001` and `KD-SPOT-RAW-NORMAL-001`, use `LIMIT_NORMAL` and assert page shape:

```python
body = _assert_success_envelope(response)
items = _assert_page_data(body, limit=LIMIT_NORMAL, offset=0, include_total=False)
_assert_kline_items_schema(items)
_assert_items_within_window(items)
```

- [ ] **Step 5: Strengthen existing curated page tests**

For `KD-USDM-CURATED-NORMAL-001` and `KD-SPOT-CURATED-NORMAL-001`, use `LIMIT_NORMAL` and assert quality flag behavior:

```python
body = _assert_success_envelope(response)
items = _assert_page_data(body, limit=LIMIT_NORMAL)
_assert_kline_items_schema(items, curated=True)
_assert_items_within_window(items)
_assert_quality_flag_matches(items, "OK")
```

- [ ] **Step 6: Convert existing raw `limit=200001` test to the correct documented location**

The current USDM raw boundary test for `limit=200001` does not match the document. Move that intent to `KD-USDM-CURATED-PARAM-003` against `get_usdm_kline`, or leave the raw variant as extra coverage with a non-`KD-*` Case ID. The documented curated limit case should look like this:

```python
@allure.title("KD-USDM-CURATED-PARAM-003 - 验证 USDM curated K线分页 limit=200001 返回参数错误。")
@pytest.mark.kline_api
def test_kd_usdm_curated_param_003_limit_too_large(kline_api):
    response = _request_allowing_http_error(
        kline_api.get_usdm_kline,
        symbol=SYMBOL,
        limit=LIMIT_TOO_LARGE,
    )
    _assert_error_response(response, "limit")
```

- [ ] **Step 7: Verify collection**

Run:

```bash
pytest --collect-only -q tests/test_kline_api.py
```

Expected:

```text
all renamed and added tests are collected; no duplicate Python function names
```

- [ ] **Step 8: Commit**

```bash
git add tests/test_kline_api.py
git commit -m "test: align kline api case ids with docs"
```

## Task 3: Fill `/kline/fetch` Missing ParamError Cases

**Files:**
- Modify: `tests/test_kline_api.py`

- [ ] **Step 1: Add missing fetch ParamError tests**

Add:

```python
@allure.title("KD-FETCH-PARAM-002 - 验证 /kline/fetch symbol 为空字符串时不返回成功数据。")
@pytest.mark.kline_api
def test_kd_fetch_param_002_empty_symbol(kline_api):
    response = _request_allowing_http_error(
        kline_api.fetch_kline,
        symbol="",
        interval=INTERVAL,
        source="binance",
    )
    _assert_business_error_or_empty_data(response, "symbol")


@allure.title("KD-FETCH-PARAM-003 - 验证 /kline/fetch source=unknown 时不 500。")
@pytest.mark.kline_api
def test_kd_fetch_param_003_unknown_source(kline_api):
    response = _request_allowing_http_error(
        kline_api.fetch_kline,
        symbol=SYMBOL,
        interval=INTERVAL,
        source="unknown",
    )
    _assert_business_error_or_empty_data(response, "source")
```

- [ ] **Step 2: Run fetch-only tests**

Run:

```bash
pytest tests/test_kline_api.py -k "fetch" -m "kline_api and not performance"
```

Expected:

```text
PASS when BASE_URL points to the test service, or SKIPPED when BASE_URL is not configured
```

- [ ] **Step 3: Commit**

```bash
git add tests/test_kline_api.py
git commit -m "test: cover kline fetch parameter errors"
```

## Task 4: Fill Time-Range Boundary, Invalid Symbol, And DataQuality Cases

**Files:**
- Modify: `tests/test_kline_api.py`

- [ ] **Step 1: Add USDM time-range missing cases**

Add:

```python
@allure.title("KD-USDM-TIMERANGE-BOUNDARY-002 - 验证 USDM 时间边界 interval=1h 不 500 且结构正确。")
@pytest.mark.kline_api
def test_kd_usdm_timerange_boundary_002_interval_1h(kline_api):
    response = _request_allowing_http_error(
        kline_api.get_usdm_time_range,
        symbol=SYMBOL,
        interval="1h",
    )
    body = _assert_not_server_error(response)
    if str(body.get("code")) == "200":
        _assert_time_range_data(body)


@allure.title("KD-USDM-TIMERANGE-PARAM-002 - 验证 USDM 时间边界非法 symbol 不 500。")
@pytest.mark.kline_api
def test_kd_usdm_timerange_param_002_invalid_symbol(kline_api):
    response = _request_allowing_http_error(
        kline_api.get_usdm_time_range,
        symbol=INVALID_SYMBOL,
        interval=INTERVAL,
    )
    body = _assert_not_server_error(response)
    if str(body.get("code")) == "200":
        _assert_time_range_data(body)
        for section_name in ("raw", "curated"):
            assert body["data"][section_name]["has_data"] is False


@allure.title("KD-USDM-TIMERANGE-DQC-001 - 验证 USDM 时间边界毫秒时间戳和 min/max 顺序。")
@pytest.mark.kline_api
@pytest.mark.dqc
def test_kd_usdm_timerange_dqc_001(kline_api):
    response = kline_api.get_usdm_time_range(symbol=SYMBOL, interval=INTERVAL)
    body = _assert_success_envelope(response)
    _assert_time_range_data(body)
```

- [ ] **Step 2: Add Spot time-range DataQuality case**

Add:

```python
@allure.title("KD-SPOT-TIMERANGE-DQC-001 - 验证 Spot 时间边界毫秒时间戳和 min/max 顺序。")
@pytest.mark.kline_api
@pytest.mark.dqc
def test_kd_spot_timerange_dqc_001(kline_api):
    response = kline_api.get_spot_time_range(symbol=SYMBOL, interval=INTERVAL)
    body = _assert_success_envelope(response)
    _assert_time_range_data(body)
```

- [ ] **Step 3: Run time-range tests**

Run:

```bash
pytest tests/test_kline_api.py -k "timerange or time_range" -m "kline_api and not performance"
```

Expected:

```text
PASS when BASE_URL points to the test service, or SKIPPED when BASE_URL is not configured
```

- [ ] **Step 4: Commit**

```bash
git add tests/test_kline_api.py
git commit -m "test: cover kline time range edge cases"
```

## Task 5: Fill USDM Raw Pagination, ParamError, DataQuality, And Logic Cases

**Files:**
- Modify: `tests/test_kline_api.py`

- [ ] **Step 1: Add USDM raw boundary cases**

Add:

```python
@allure.title("KD-USDM-RAW-BOUNDARY-001 - 验证 USDM raw limit=1 offset=0 分页回显正确。")
@pytest.mark.kline_api
def test_kd_usdm_raw_boundary_001_limit_one(kline_api):
    response = kline_api.get_usdm_kline_raw(
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time_ms=START_TIME_MS,
        end_time_ms=END_TIME_MS,
        limit=LIMIT_SMALL,
        offset=0,
    )
    body = _assert_success_envelope(response)
    _assert_page_data(body, limit=LIMIT_SMALL, offset=0)


@allure.title("KD-USDM-RAW-BOUNDARY-002 - 验证 USDM raw include_total=true 分页回显正确。")
@pytest.mark.kline_api
def test_kd_usdm_raw_boundary_002_include_total(kline_api):
    response = kline_api.get_usdm_kline_raw(
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time_ms=START_TIME_MS,
        end_time_ms=END_TIME_MS,
        limit=LIMIT_SMALL,
        include_total=True,
    )
    body = _assert_success_envelope(response)
    _assert_page_data(body, limit=LIMIT_SMALL, offset=0, include_total=True)


@allure.title("KD-USDM-RAW-BOUNDARY-003 - 验证 USDM raw 不传时间窗时不 500。")
@pytest.mark.kline_api
def test_kd_usdm_raw_boundary_003_without_time_window(kline_api):
    response = _request_allowing_http_error(
        kline_api.get_usdm_kline_raw,
        symbol=SYMBOL,
        interval=INTERVAL,
        limit=LIMIT_NORMAL,
    )
    body = _assert_not_server_error(response)
    if str(body.get("code")) == "200":
        _assert_page_data(body, limit=LIMIT_NORMAL, offset=0)
```

- [ ] **Step 2: Add USDM raw missing ParamError cases**

Add:

```python
@pytest.mark.parametrize(
    ("case_id", "kwargs", "expected_text"),
    [
        (
            "KD-USDM-RAW-PARAM-002",
            {"symbol": SYMBOL, "start_time_ms": START_TIME_MS},
            "time",
        ),
        (
            "KD-USDM-RAW-PARAM-003",
            {"symbol": SYMBOL, "start_time_ms": END_TIME_MS, "end_time_ms": START_TIME_MS},
            "time",
        ),
        (
            "KD-USDM-RAW-PARAM-004",
            {"symbol": SYMBOL, "limit": 0},
            "limit",
        ),
        (
            "KD-USDM-RAW-PARAM-005",
            {"symbol": SYMBOL, "offset": -1},
            "offset",
        ),
    ],
)
@pytest.mark.kline_api
def test_kd_usdm_raw_param_errors(kline_api, case_id, kwargs, expected_text):
    allure.dynamic.title(f"{case_id} - 验证 USDM raw 参数错误。")
    response = _request_allowing_http_error(kline_api.get_usdm_kline_raw, **kwargs)
    _assert_error_response(response, expected_text)
```

- [ ] **Step 3: Strengthen response, DQC, and logic cases**

Add or update:

```python
@allure.title("KD-USDM-RAW-DQC-001 - 验证 USDM raw timestamp、数值字段和时间排序。")
@pytest.mark.kline_api
@pytest.mark.dqc
def test_kd_usdm_raw_dqc_001(kline_api):
    response = kline_api.get_usdm_kline_raw(
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time_ms=START_TIME_MS,
        end_time_ms=END_TIME_MS,
        limit=LIMIT_NORMAL,
    )
    body = _assert_success_envelope(response)
    items = _assert_page_data(body, limit=LIMIT_NORMAL, offset=0)
    _assert_kline_items_schema(items)
    _assert_items_sorted_by_timestamp(items)


@allure.title("KD-USDM-RAW-LOGIC-001 - 验证 USDM raw OHLC、symbol、interval 和时间窗过滤。")
@pytest.mark.kline_api
@pytest.mark.logic
def test_kd_usdm_raw_logic_001(kline_api):
    response = kline_api.get_usdm_kline_raw(
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time_ms=START_TIME_MS,
        end_time_ms=END_TIME_MS,
        limit=LIMIT_NORMAL,
    )
    body = _assert_success_envelope(response)
    items = _assert_page_data(body, limit=LIMIT_NORMAL, offset=0)
    _assert_kline_items_schema(items)
    _assert_items_within_window(items)
```

- [ ] **Step 4: Run USDM raw tests**

Run:

```bash
pytest tests/test_kline_api.py -k "usdm_raw" -m "kline_api and not performance"
```

Expected:

```text
PASS when BASE_URL points to the test service, or SKIPPED when BASE_URL is not configured
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_kline_api.py
git commit -m "test: cover usdm raw kline contract"
```

## Task 6: Fill USDM Curated Boundary, ParamError, DataQuality, And Logic Cases

**Files:**
- Modify: `tests/test_kline_api.py`

- [ ] **Step 1: Add curated boundary cases**

Add:

```python
@allure.title("KD-USDM-CURATED-BOUNDARY-001 - 验证 USDM curated 不传 quality_flag 时结构正确。")
@pytest.mark.kline_api
def test_kd_usdm_curated_boundary_001_without_quality_flag(kline_api):
    response = kline_api.get_usdm_kline(
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time_ms=START_TIME_MS,
        end_time_ms=END_TIME_MS,
        quality_flag=None,
        limit=LIMIT_NORMAL,
    )
    body = _assert_success_envelope(response)
    items = _assert_page_data(body, limit=LIMIT_NORMAL, offset=0)
    _assert_kline_items_schema(items, curated=True)


@allure.title("KD-USDM-CURATED-BOUNDARY-002 - 验证 USDM curated quality_flag=ok 不 500。")
@pytest.mark.kline_api
def test_kd_usdm_curated_boundary_002_quality_flag_lowercase(kline_api):
    response = _request_allowing_http_error(
        kline_api.get_usdm_kline,
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time_ms=START_TIME_MS,
        end_time_ms=END_TIME_MS,
        quality_flag="ok",
        limit=LIMIT_NORMAL,
    )
    body = _assert_not_server_error(response)
    if str(body.get("code")) == "200":
        items = _assert_page_data(body, limit=LIMIT_NORMAL, offset=0)
        _assert_quality_flag_matches(items, "OK")
```

- [ ] **Step 2: Add curated ParamError cases**

Add:

```python
@allure.title("KD-USDM-CURATED-PARAM-002 - 验证 USDM curated 只传 end_time_ms 返回时间窗参数错误。")
@pytest.mark.kline_api
def test_kd_usdm_curated_param_002_only_end_time(kline_api):
    response = _request_allowing_http_error(
        kline_api.get_usdm_kline,
        symbol=SYMBOL,
        end_time_ms=END_TIME_MS,
    )
    _assert_error_response(response, "time")


@allure.title("KD-USDM-CURATED-PARAM-003 - 验证 USDM curated limit=200001 返回参数错误。")
@pytest.mark.kline_api
def test_kd_usdm_curated_param_003_limit_too_large(kline_api):
    response = _request_allowing_http_error(
        kline_api.get_usdm_kline,
        symbol=SYMBOL,
        limit=LIMIT_TOO_LARGE,
    )
    _assert_error_response(response, "limit")
```

- [ ] **Step 3: Add curated DQC and logic cases**

Add:

```python
@allure.title("KD-USDM-CURATED-DQC-001 - 验证 USDM curated timestamp、close_time 和数值字段。")
@pytest.mark.kline_api
@pytest.mark.dqc
def test_kd_usdm_curated_dqc_001(kline_api):
    response = kline_api.get_usdm_kline(
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time_ms=START_TIME_MS,
        end_time_ms=END_TIME_MS,
        limit=LIMIT_NORMAL,
    )
    body = _assert_success_envelope(response)
    items = _assert_page_data(body, limit=LIMIT_NORMAL, offset=0)
    _assert_kline_items_schema(items, curated=True)


@allure.title("KD-USDM-CURATED-LOGIC-001 - 验证 USDM curated OHLC 和时间窗过滤。")
@pytest.mark.kline_api
@pytest.mark.logic
def test_kd_usdm_curated_logic_001(kline_api):
    response = kline_api.get_usdm_kline(
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time_ms=START_TIME_MS,
        end_time_ms=END_TIME_MS,
        limit=LIMIT_NORMAL,
    )
    body = _assert_success_envelope(response)
    items = _assert_page_data(body, limit=LIMIT_NORMAL, offset=0)
    _assert_kline_items_schema(items, curated=True)
    _assert_items_within_window(items)
```

- [ ] **Step 4: Run USDM curated tests**

Run:

```bash
pytest tests/test_kline_api.py -k "usdm_curated" -m "kline_api and not performance"
```

Expected:

```text
PASS when BASE_URL points to the test service, or SKIPPED when BASE_URL is not configured
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_kline_api.py
git commit -m "test: cover usdm curated kline contract"
```

## Task 7: Fill Spot Raw Boundary, ParamError, DataQuality, And Logic Cases

**Files:**
- Modify: `tests/test_kline_api.py`

- [ ] **Step 1: Correct the existing Spot raw limit error Case ID**

The current Spot raw `limit=0` test should be identified as `KD-SPOT-RAW-PARAM-003`, not a boundary case:

```python
@allure.title("KD-SPOT-RAW-PARAM-003 - 验证 Spot raw limit=0 返回参数错误。")
@pytest.mark.kline_api
def test_kd_spot_raw_param_003_limit_zero(kline_api):
    response = _request_allowing_http_error(
        kline_api.get_spot_kline_raw,
        symbol=SYMBOL,
        limit=0,
    )
    _assert_error_response(response, "limit")
```

- [ ] **Step 2: Add Spot raw boundary cases**

Add:

```python
@allure.title("KD-SPOT-RAW-BOUNDARY-001 - 验证 Spot raw limit=1 最多返回 1 条。")
@pytest.mark.kline_api
def test_kd_spot_raw_boundary_001_limit_one(kline_api):
    response = kline_api.get_spot_kline_raw(
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time_ms=START_TIME_MS,
        end_time_ms=END_TIME_MS,
        limit=LIMIT_SMALL,
    )
    body = _assert_success_envelope(response)
    _assert_page_data(body, limit=LIMIT_SMALL, offset=0)


@allure.title("KD-SPOT-RAW-BOUNDARY-002 - 验证 Spot raw include_total=true。")
@pytest.mark.kline_api
def test_kd_spot_raw_boundary_002_include_total(kline_api):
    response = kline_api.get_spot_kline_raw(
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time_ms=START_TIME_MS,
        end_time_ms=END_TIME_MS,
        limit=LIMIT_SMALL,
        include_total=True,
    )
    body = _assert_success_envelope(response)
    _assert_page_data(body, limit=LIMIT_SMALL, offset=0, include_total=True)
```

- [ ] **Step 3: Add Spot raw missing ParamError and quality cases**

Add:

```python
@allure.title("KD-SPOT-RAW-PARAM-002 - 验证 Spot raw 时间窗只传一端返回参数错误。")
@pytest.mark.kline_api
def test_kd_spot_raw_param_002_only_start_time(kline_api):
    response = _request_allowing_http_error(
        kline_api.get_spot_kline_raw,
        symbol=SYMBOL,
        start_time_ms=START_TIME_MS,
    )
    _assert_error_response(response, "time")


@allure.title("KD-SPOT-RAW-DQC-001 - 验证 Spot raw timestamp 和数值字段。")
@pytest.mark.kline_api
@pytest.mark.dqc
def test_kd_spot_raw_dqc_001(kline_api):
    response = kline_api.get_spot_kline_raw(
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time_ms=START_TIME_MS,
        end_time_ms=END_TIME_MS,
        limit=LIMIT_NORMAL,
    )
    body = _assert_success_envelope(response)
    items = _assert_page_data(body, limit=LIMIT_NORMAL, offset=0)
    _assert_kline_items_schema(items)


@allure.title("KD-SPOT-RAW-LOGIC-001 - 验证 Spot raw OHLC、symbol 和 interval。")
@pytest.mark.kline_api
@pytest.mark.logic
def test_kd_spot_raw_logic_001(kline_api):
    response = kline_api.get_spot_kline_raw(
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time_ms=START_TIME_MS,
        end_time_ms=END_TIME_MS,
        limit=LIMIT_NORMAL,
    )
    body = _assert_success_envelope(response)
    items = _assert_page_data(body, limit=LIMIT_NORMAL, offset=0)
    _assert_kline_items_schema(items)
    _assert_items_within_window(items)
```

- [ ] **Step 4: Run Spot raw tests**

Run:

```bash
pytest tests/test_kline_api.py -k "spot_raw" -m "kline_api and not performance"
```

Expected:

```text
PASS when BASE_URL points to the test service, or SKIPPED when BASE_URL is not configured
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_kline_api.py
git commit -m "test: cover spot raw kline contract"
```

## Task 8: Fill Spot Curated Boundary, DataQuality, And Logic Cases

**Files:**
- Modify: `tests/test_kline_api.py`

- [ ] **Step 1: Correct existing Spot curated reverse-time Case ID**

The current Spot curated reverse-time test should be identified as `KD-SPOT-CURATED-PARAM-002`:

```python
@allure.title("KD-SPOT-CURATED-PARAM-002 - 验证 Spot curated end_time_ms <= start_time_ms 返回参数错误。")
@pytest.mark.kline_api
def test_kd_spot_curated_param_002_reversed_time_window(kline_api):
    response = _request_allowing_http_error(
        kline_api.get_spot_kline,
        symbol=SYMBOL,
        start_time_ms=END_TIME_MS,
        end_time_ms=START_TIME_MS,
    )
    _assert_error_response(response, "time")
```

- [ ] **Step 2: Add Spot curated boundary cases**

Add:

```python
@allure.title("KD-SPOT-CURATED-BOUNDARY-001 - 验证 Spot curated 不传 quality_flag 时结构正确。")
@pytest.mark.kline_api
def test_kd_spot_curated_boundary_001_without_quality_flag(kline_api):
    response = kline_api.get_spot_kline(
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time_ms=START_TIME_MS,
        end_time_ms=END_TIME_MS,
        quality_flag=None,
        limit=LIMIT_NORMAL,
    )
    body = _assert_success_envelope(response)
    items = _assert_page_data(body, limit=LIMIT_NORMAL, offset=0)
    _assert_kline_items_schema(items, curated=True)


@allure.title("KD-SPOT-CURATED-BOUNDARY-002 - 验证 Spot curated quality_flag=ok 不 500。")
@pytest.mark.kline_api
def test_kd_spot_curated_boundary_002_quality_flag_lowercase(kline_api):
    response = _request_allowing_http_error(
        kline_api.get_spot_kline,
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time_ms=START_TIME_MS,
        end_time_ms=END_TIME_MS,
        quality_flag="ok",
        limit=LIMIT_NORMAL,
    )
    body = _assert_not_server_error(response)
    if str(body.get("code")) == "200":
        items = _assert_page_data(body, limit=LIMIT_NORMAL, offset=0)
        _assert_quality_flag_matches(items, "OK")
```

- [ ] **Step 3: Add Spot curated DQC and logic cases**

Add:

```python
@allure.title("KD-SPOT-CURATED-DQC-001 - 验证 Spot curated timestamp 和数值字段。")
@pytest.mark.kline_api
@pytest.mark.dqc
def test_kd_spot_curated_dqc_001(kline_api):
    response = kline_api.get_spot_kline(
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time_ms=START_TIME_MS,
        end_time_ms=END_TIME_MS,
        limit=LIMIT_NORMAL,
    )
    body = _assert_success_envelope(response)
    items = _assert_page_data(body, limit=LIMIT_NORMAL, offset=0)
    _assert_kline_items_schema(items, curated=True)


@allure.title("KD-SPOT-CURATED-LOGIC-001 - 验证 Spot curated OHLC 和时间窗过滤。")
@pytest.mark.kline_api
@pytest.mark.logic
def test_kd_spot_curated_logic_001(kline_api):
    response = kline_api.get_spot_kline(
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time_ms=START_TIME_MS,
        end_time_ms=END_TIME_MS,
        limit=LIMIT_NORMAL,
    )
    body = _assert_success_envelope(response)
    items = _assert_page_data(body, limit=LIMIT_NORMAL, offset=0)
    _assert_kline_items_schema(items, curated=True)
    _assert_items_within_window(items)
```

- [ ] **Step 4: Run Spot curated tests**

Run:

```bash
pytest tests/test_kline_api.py -k "spot_curated" -m "kline_api and not performance"
```

Expected:

```text
PASS when BASE_URL points to the test service, or SKIPPED when BASE_URL is not configured
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_kline_api.py
git commit -m "test: cover spot curated kline contract"
```

## Task 9: Final Coverage Audit And Full Verification

**Files:**
- Modify: `tests/test_kline_api.py`

- [ ] **Step 1: Audit every documented Case ID**

Run:

```bash
grep -o "KD-[A-Z0-9-]*" docs/test_cases_kline_data.md | sort -u > /tmp/kline_doc_cases.txt
grep -o "KD-[A-Z0-9-]*" tests/test_kline_api.py | sort -u > /tmp/kline_test_cases.txt
comm -23 /tmp/kline_doc_cases.txt /tmp/kline_test_cases.txt
```

Expected:

```text
no output
```

- [ ] **Step 2: Collect all tests**

Run:

```bash
pytest --collect-only -q tests/test_kline_api.py
```

Expected:

```text
collection succeeds; documented cases and existing extra performance tests are visible
```

- [ ] **Step 3: Run non-performance Kline tests**

Run:

```bash
pytest tests/test_kline_api.py -m "kline_api and not performance"
```

Expected:

```text
PASS when BASE_URL points to the test service, or SKIPPED when BASE_URL is not configured
```

- [ ] **Step 4: Run performance tests separately**

Run:

```bash
pytest tests/test_kline_api.py -m "kline_api and performance"
```

Expected:

```text
PASS when the service is responsive under the 2 second baseline, or SKIPPED when BASE_URL is not configured
```

- [ ] **Step 5: Commit final audit fixes**

```bash
git add tests/test_kline_api.py
git commit -m "test: complete kline api documented coverage"
```

## Self-Review

- Spec coverage: The plan maps the documented Case IDs from `/kline/fetch`, USDM time-range, USDM raw, USDM curated, Spot time-range, Spot raw, and Spot curated into test edits.
- Placeholder scan: No implementation step relies on unspecified helper behavior; helper code and test code are included in the relevant tasks.
- Type consistency: Helper names and constants are defined before use. API method names match `api_services/kline_data_api.py`.
- Residual risk: Several assertions assume the documented response shape `data.filters`, `data.pagination`, `data.items`, `data.raw`, and `data.curated` is correct. If the live service currently differs, the new tests should fail and force either API correction or test-case document correction.
