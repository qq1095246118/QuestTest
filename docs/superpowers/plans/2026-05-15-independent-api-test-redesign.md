# Independent API Test Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign every API test file except `tests/test_kline_api.py` so every documented Case ID is implemented as an independent pytest function with the same upper/lower-layer style used by `test_kline_api.py`.

**Architecture:** Tests become the upper layer: fixtures create domain API service objects, each case calls one service method, and assertions stay inside the case function. API service files become the lower layer: they own endpoint paths, request method selection, parameter cleanup, and `HTTPClient` calls. Test files must not parse docs at runtime, must not call `requests` directly, and must not use shared assertion helpers to cover multiple cases.

**Tech Stack:** Python 3, pytest, allure-pytest, requests `HTTPError`, existing `core.http_client.HTTPClient`, existing `config.settings`.

---

## Scope And Non-Negotiable Rules

- Do not modify `tests/test_kline_api.py`.
- Rewrite these files only after their service layer exists:
  - `tests/test_binance_full_api.py`
  - `tests/test_coinglass_api.py`
  - `tests/test_factor_data_api.py`
  - `tests/test_binance_usdm_api.py`
  - `tests/test_open_interest_api.py`
- Each Case ID from the matching doc must have one independent test function.
- Function names use lowercase Case ID with hyphens converted to underscores, prefixed by `test_`.
- A test function may loop over multiple documented endpoints inside the same Case ID when the doc says "两个接口", "三个接口", or "六个接口". The Case ID still remains one independent pytest case.
- Do not use `@pytest.mark.parametrize` to represent multiple Case IDs.
- Do not use `_load_case_metadata`, `CASE_METADATA`, or runtime docs parsing in test files.
- Do not use `_apply_case_allure`; Allure metadata must be static decorators or direct calls inside the case.
- Do not import or call `requests` in test files.
- Do not create shared assertion helpers in tests, including `_assert_success_envelope`, `_assert_pagination`, `_assert_ohlc`, or equivalent.
- Each case must contain its own response envelope assertions, status assertions, schema assertions, and case-specific assertions inline.
- Sorting assertions are allowed only when request parameters or docs explicitly define `sort` or `order`.
- ParamError/no500 cases must guard against 5xx and must prove the response is not a successful data response when validation fails.
- DataQuality, BusinessLogic, and Performance cases must use `@pytest.mark.dqc`, `@pytest.mark.logic`, and `@pytest.mark.performance` respectively.

## File Structure

- Create: `api_services/binance_full_api.py`
  - Lower-layer wrapper for `/api/binance-full/...`.
- Create: `api_services/coinglass_api.py`
  - Lower-layer wrapper for `/coinglass/funding-rate/...`, `/coinglass/long-short-ratio/...`, and `/coinglass/controlled_coin_summary`.
- Create: `api_services/factor_data_api.py`
  - Lower-layer wrapper for `/api/v1/factor-data/query`.
- Create: `api_services/binance_usdm_api.py`
  - Lower-layer wrapper for internal `/api/usdm/volume-rank` and `/api/usdm/top-gainers`.
- Create: `api_services/open_interest_api.py`
  - Lower-layer wrapper for `/coinglass/oi/...`.
- Modify: `pytest.ini`
  - Register the new domain markers.
- Rewrite: `tests/test_binance_usdm_api.py`
  - 28 independent BUSDM cases.
- Rewrite: `tests/test_factor_data_api.py`
  - 34 independent FD cases.
- Rewrite: `tests/test_coinglass_api.py`
  - 44 independent CG cases.
- Rewrite: `tests/test_open_interest_api.py`
  - 42 independent OI cases.
- Rewrite: `tests/test_binance_full_api.py`
  - 84 independent BF cases.

---

### Task 1: Add Domain Markers

**Files:**
- Modify: `pytest.ini`

- [ ] **Step 1: Add markers matching the new files**

Add these marker lines under the existing `markers =` block:

```ini
    binance_full_api: Run Binance Full platform API tests
    coinglass_api: Run CoinGlass platform API tests
    factor_data_api: Run Factor Data platform API tests
    binance_usdm_api: Run internal Binance USDM ranking API tests
    open_interest_api: Run Open Interest platform API tests
```

- [ ] **Step 2: Verify pytest accepts marker config**

Run:

```bash
python3 -m pytest --markers
```

Expected:

```text
@pytest.mark.binance_full_api: Run Binance Full platform API tests
@pytest.mark.coinglass_api: Run CoinGlass platform API tests
@pytest.mark.factor_data_api: Run Factor Data platform API tests
@pytest.mark.binance_usdm_api: Run internal Binance USDM ranking API tests
@pytest.mark.open_interest_api: Run Open Interest platform API tests
```

- [ ] **Step 3: Commit marker registration**

Run:

```bash
git add pytest.ini
git commit -m "test: register API domain pytest markers"
```

Expected: commit succeeds with only `pytest.ini` staged.

---

### Task 2: Create Lower-Layer API Service Wrappers

**Files:**
- Create: `api_services/binance_full_api.py`
- Create: `api_services/coinglass_api.py`
- Create: `api_services/factor_data_api.py`
- Create: `api_services/binance_usdm_api.py`
- Create: `api_services/open_interest_api.py`

- [ ] **Step 1: Create shared service shape in each new file**

Each file must follow the same shape as `KlineDataAPI`: constructor, `get`, `post` when needed, endpoint methods, and local `_clean`.

Use this service skeleton in every new service file, with class name and methods adjusted per domain:

```python
from __future__ import annotations

from typing import Any

from config.settings import settings
from core.http_client import HTTPClient


class DomainAPI:
    def __init__(self):
        self.base_url = settings.base_url
        self.headers = {"Content-Type": "application/json"}

    def get(self, endpoint: str, params: dict[str, Any] | None = None):
        url = f"{self.base_url}{endpoint}"
        return HTTPClient.request("GET", url, headers=self.headers, params=_clean(params))

    def post(
        self,
        endpoint: str,
        json: dict[str, Any] | None = None,
        data=None,
        params: dict[str, Any] | None = None,
    ):
        url = f"{self.base_url}{endpoint}"
        return HTTPClient.request(
            "POST",
            url,
            headers=self.headers,
            json=json,
            data=data,
            params=_clean(params),
        )


def _clean(params: dict[str, Any] | None) -> dict[str, Any]:
    if not params:
        return {}
    return {key: value for key, value in params.items() if value is not None}
```

- [ ] **Step 2: Implement `BinanceUSDMAPI` endpoints**

Create `api_services/binance_usdm_api.py` with:

```python
class BinanceUSDMAPI:
    def get_volume_rank(
        self,
        range_unit: Any = "hours",
        n: Any = 24,
        top_k: Any = 10,
        use_quote_volume: Any = True,
        m_days: Any = 7,
        include_ticker_24h: Any = True,
    ):
        return self.get(
            "/api/usdm/volume-rank",
            {
                "range_unit": range_unit,
                "n": n,
                "top_k": top_k,
                "use_quote_volume": use_quote_volume,
                "m_days": m_days,
                "include_ticker_24h": include_ticker_24h,
            },
        )

    def get_top_gainers(
        self,
        change_threshold: Any = 5,
        days_history: Any = 10,
        limit: Any = 10,
    ):
        return self.get(
            "/api/usdm/top-gainers",
            {
                "change_threshold": change_threshold,
                "days_history": days_history,
                "limit": limit,
            },
        )
```

- [ ] **Step 3: Implement `FactorDataAPI` endpoint**

Create `api_services/factor_data_api.py` with:

```python
class FactorDataAPI:
    def query(
        self,
        dataset: Any = "kline_data_future",
        symbols: Any = None,
        interval: Any = "1m",
        start_time_ms: Any = None,
        end_time_ms: Any = None,
        fields: Any = None,
        quality_flags: Any = None,
        page_size: Any = 100,
        cursor: Any = None,
        sort: Any = "asc",
        include_symbol_coverage: Any = False,
    ):
        return self.post(
            "/api/v1/factor-data/query",
            json=_clean(
                {
                    "dataset": dataset,
                    "symbols": symbols,
                    "interval": interval,
                    "start_time_ms": start_time_ms,
                    "end_time_ms": end_time_ms,
                    "fields": fields,
                    "quality_flags": quality_flags,
                    "page_size": page_size,
                    "cursor": cursor,
                    "sort": sort,
                    "include_symbol_coverage": include_symbol_coverage,
                }
            ),
        )
```

- [ ] **Step 4: Implement `CoinGlassAPI` endpoints**

Create `api_services/coinglass_api.py` with endpoint methods:

```python
class CoinGlassAPI:
    def get_funding_rate_ohlc_history(self, symbol: Any = None, interval: Any = None, limit: Any = None):
        return self.get("/coinglass/funding-rate/ohlc-history", {"symbol": symbol, "interval": interval, "limit": limit})

    def get_funding_rate_exchange_list(self, symbol: Any = None, limit: Any = None):
        return self.get("/coinglass/funding-rate/exchange-list", {"symbol": symbol, "limit": limit})

    def get_funding_rate_arbitrage(self, symbol: Any = None, limit: Any = None):
        return self.get("/coinglass/funding-rate/arbitrage", {"symbol": symbol, "limit": limit})

    def get_funding_rate_summary(self, symbol: Any = None, limit: Any = None):
        return self.get("/coinglass/funding-rate/summary", {"symbol": symbol, "limit": limit})

    def get_long_short_ratio_history(
        self,
        exchange: Any = None,
        symbol: Any = None,
        interval: Any = None,
        start_time: Any = None,
        end_time: Any = None,
        limit: Any = None,
    ):
        return self.get(
            "/coinglass/long-short-ratio/history",
            {
                "exchange": exchange,
                "symbol": symbol,
                "interval": interval,
                "start_time": start_time,
                "end_time": end_time,
                "limit": limit,
            },
        )

    def get_controlled_coin_summary(self, symbol: Any = None, exchange: Any = None, interval: Any = None):
        return self.get("/coinglass/controlled_coin_summary", {"symbol": symbol, "exchange": exchange, "interval": interval})
```

- [ ] **Step 5: Implement `OpenInterestAPI` endpoints**

Create `api_services/open_interest_api.py` with endpoint methods:

```python
class OpenInterestAPI:
    def get_history(
        self,
        exchange: Any = None,
        symbol: Any = None,
        interval: Any = None,
        start_time: Any = None,
        end_time: Any = None,
        unit: Any = None,
        force_refresh: Any = None,
        limit: Any = None,
    ):
        return self.get(
            "/coinglass/oi/history",
            {
                "exchange": exchange,
                "symbol": symbol,
                "interval": interval,
                "start_time": start_time,
                "end_time": end_time,
                "unit": unit,
                "force_refresh": force_refresh,
                "limit": limit,
            },
        )

    def get_aggregated_history(self, symbol: Any = None, interval: Any = None, start_time: Any = None, end_time: Any = None, unit: Any = None, limit: Any = None):
        return self.get("/coinglass/oi/aggregated/history", {"symbol": symbol, "interval": interval, "start_time": start_time, "end_time": end_time, "unit": unit, "limit": limit})

    def get_exchanges(self, symbol: Any = None, interval: Any = None, unit: Any = None, limit: Any = None):
        return self.get("/coinglass/oi/exchanges", {"symbol": symbol, "interval": interval, "unit": unit, "limit": limit})

    def get_orderbook_aggregated_history(
        self,
        exchange_list: Any = None,
        symbol: Any = None,
        interval: Any = None,
        range: Any = None,
        start_time: Any = None,
        end_time: Any = None,
        limit: Any = None,
    ):
        return self.get(
            "/coinglass/oi/orderbook/aggregated-history",
            {
                "exchange_list": exchange_list,
                "symbol": symbol,
                "interval": interval,
                "range": range,
                "start_time": start_time,
                "end_time": end_time,
                "limit": limit,
            },
        )

    def get_summary(self, symbol: Any = None, exchange: Any = None, interval: Any = None, limit: Any = None):
        return self.get("/coinglass/oi/summary", {"symbol": symbol, "exchange": exchange, "interval": interval, "limit": limit})
```

- [ ] **Step 6: Implement `BinanceFullAPI` endpoints**

Create `api_services/binance_full_api.py` with endpoint methods for every BF group:

```python
class BinanceFullAPI:
    def get_meta_tables(self):
        return self.get("/api/binance-full/meta/tables")

    def get_usdm_registry_symbols(self, contract_type: Any = None, quote_asset: Any = None, status: Any = None):
        return self.get("/api/binance-full/usdm/registry/symbols", {"contract_type": contract_type, "quote_asset": quote_asset, "status": status})

    def get_usdm_complete_symbols(self, start_time_ms: Any = None, end_time_ms: Any = None, limit: Any = None, include_legacy_coinm_in_usdm_aggregate: Any = None):
        return self.get("/api/binance-full/usdm/meta/complete-symbols", {"start_time_ms": start_time_ms, "end_time_ms": end_time_ms, "limit": limit, "include_legacy_coinm_in_usdm_aggregate": include_legacy_coinm_in_usdm_aggregate})

    def get_usdm_delisted_symbols(self, status: Any = None, limit: Any = None, include_disabled_only: Any = None):
        return self.get("/api/binance-full/usdm/meta/delisted-symbols", {"status": status, "limit": limit, "include_disabled_only": include_disabled_only})

    def get_usdm_time_range(self, symbol: Any = None, interval: Any = None):
        return self.get("/api/binance-full/usdm/meta/time-range", {"symbol": symbol, "interval": interval})

    def get_coinm_perp_time_range(self, pair: Any = None, contract_type: Any = None, interval: Any = None):
        return self.get("/api/binance-full/coinm-perp/meta/time-range", {"pair": pair, "contract_type": contract_type, "interval": interval})

    def get_coinm_delivery_time_range(self, pair: Any = None, contract_type: Any = None, interval: Any = None):
        return self.get("/api/binance-full/coinm-delivery/meta/time-range", {"pair": pair, "contract_type": contract_type, "interval": interval})

    def get_usdm_delivery_time_range(self, pair: Any = None, contract_type: Any = None, interval: Any = None):
        return self.get("/api/binance-full/usdm-delivery/meta/time-range", {"pair": pair, "contract_type": contract_type, "interval": interval})

    def get_usdm_kline(self, symbol: Any = None, interval: Any = None, start_time_ms: Any = None, end_time_ms: Any = None, limit: Any = None, offset: Any = None, include_total: Any = None):
        return self.get("/api/binance-full/usdm/kline", {"symbol": symbol, "interval": interval, "start_time_ms": start_time_ms, "end_time_ms": end_time_ms, "limit": limit, "offset": offset, "include_total": include_total})

    def get_usdm_kline_1h_all_symbols(self, start_time_ms: Any = None, end_time_ms: Any = None, symbol: Any = None, order: Any = None):
        return self.get("/api/binance-full/usdm/kline-1h/all-symbols", {"start_time_ms": start_time_ms, "end_time_ms": end_time_ms, "symbol": symbol, "order": order})

    def get_coinm_perp_kline(self, pair: Any = None, contract_type: Any = None, interval: Any = None, start_time_ms: Any = None, end_time_ms: Any = None, limit: Any = None, offset: Any = None, include_total: Any = None):
        return self.get("/api/binance-full/coinm-perp/kline", {"pair": pair, "contract_type": contract_type, "interval": interval, "start_time_ms": start_time_ms, "end_time_ms": end_time_ms, "limit": limit, "offset": offset, "include_total": include_total})

    def get_coinm_delivery_kline(self, pair: Any = None, contract_type: Any = None, interval: Any = None, start_time_ms: Any = None, end_time_ms: Any = None, limit: Any = None, offset: Any = None, include_total: Any = None):
        return self.get("/api/binance-full/coinm-delivery/kline", {"pair": pair, "contract_type": contract_type, "interval": interval, "start_time_ms": start_time_ms, "end_time_ms": end_time_ms, "limit": limit, "offset": offset, "include_total": include_total})

    def get_usdm_delivery_kline(self, pair: Any = None, contract_type: Any = None, interval: Any = None, start_time_ms: Any = None, end_time_ms: Any = None, limit: Any = None, offset: Any = None, include_total: Any = None):
        return self.get("/api/binance-full/usdm-delivery/kline", {"pair": pair, "contract_type": contract_type, "interval": interval, "start_time_ms": start_time_ms, "end_time_ms": end_time_ms, "limit": limit, "offset": offset, "include_total": include_total})

    def get_usdm_funding(self, symbol: Any = None, start_time_ms: Any = None, end_time_ms: Any = None, limit: Any = None, include_legacy_coinm_in_usdm_aggregate: Any = None):
        return self.get("/api/binance-full/usdm/funding", {"symbol": symbol, "start_time_ms": start_time_ms, "end_time_ms": end_time_ms, "limit": limit, "include_legacy_coinm_in_usdm_aggregate": include_legacy_coinm_in_usdm_aggregate})

    def get_coinm_perp_funding(self, pair: Any = None, contract_type: Any = None, start_time_ms: Any = None, end_time_ms: Any = None, limit: Any = None):
        return self.get("/api/binance-full/coinm-perp/funding", {"pair": pair, "contract_type": contract_type, "start_time_ms": start_time_ms, "end_time_ms": end_time_ms, "limit": limit})

    def post_time_bounds(self, endpoint: str, symbols: Any = None, interval: Any = None, contract_type: Any = None):
        return self.post(endpoint, json=_clean({"symbols": symbols, "interval": interval}), params={"contract_type": contract_type})
```

- [ ] **Step 7: Verify services import**

Run:

```bash
python3 -m py_compile api_services/binance_full_api.py api_services/coinglass_api.py api_services/factor_data_api.py api_services/binance_usdm_api.py api_services/open_interest_api.py
```

Expected: command exits with code 0 and prints no syntax errors.

- [ ] **Step 8: Commit service wrappers**

Run:

```bash
git add api_services/binance_full_api.py api_services/coinglass_api.py api_services/factor_data_api.py api_services/binance_usdm_api.py api_services/open_interest_api.py
git commit -m "test: add platform API service wrappers"
```

Expected: commit succeeds with the five new service files staged.

---

### Task 3: Rewrite `test_binance_usdm_api.py`

**Files:**
- Modify: `tests/test_binance_usdm_api.py`
- Depends on: `api_services/binance_usdm_api.py`

- [ ] **Step 1: Replace imports, fixture, and constants**

The top of the file must use this shape:

```python
from __future__ import annotations

from decimal import Decimal
from time import perf_counter

import allure
import pytest
from requests.exceptions import HTTPError

from api_services.binance_usdm_api import BinanceUSDMAPI
from config.settings import settings

LIMIT_NORMAL = 10
PERFORMANCE_BASELINE_SECONDS = 2.0


@pytest.fixture(scope="module")
def binance_usdm_api() -> BinanceUSDMAPI:
    if not settings.base_url or "exchange.com" in settings.base_url:
        pytest.skip("Binance USDM API BASE_URL is not configured for live API tests.")
    return BinanceUSDMAPI()
```

- [ ] **Step 2: Implement every BUSDM volume Case ID as one function**

Implement independent functions for:

```text
BUSDM-VOLUME-NORMAL-001
BUSDM-VOLUME-NORMAL-002
BUSDM-VOLUME-BOUNDARY-001
BUSDM-VOLUME-BOUNDARY-002
BUSDM-VOLUME-BOUNDARY-003
BUSDM-VOLUME-BOUNDARY-004
BUSDM-VOLUME-PARAM-001
BUSDM-VOLUME-PARAM-002
BUSDM-VOLUME-PARAM-003
BUSDM-VOLUME-PARAM-004
BUSDM-VOLUME-PARAM-005
BUSDM-VOLUME-RESPONSE-001
BUSDM-VOLUME-DQC-001
BUSDM-VOLUME-LOGIC-001
BUSDM-VOLUME-PERF-001
```

Each success case must directly assert:

```python
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
```

For `BUSDM-VOLUME-LOGIC-001`, include sorting only because the business case is ranking:

```python
volumes = []
for item in data["items"]:
    volume_value = item.get("quote_volume")
    if volume_value is not None:
        volumes.append(Decimal(str(volume_value)))
if len(volumes) > 1:
    assert volumes == sorted(volumes, reverse=True)
```

- [ ] **Step 3: Implement every BUSDM gainers Case ID as one function**

Implement independent functions for:

```text
BUSDM-GAINERS-NORMAL-001
BUSDM-GAINERS-BOUNDARY-001
BUSDM-GAINERS-BOUNDARY-002
BUSDM-GAINERS-BOUNDARY-003
BUSDM-GAINERS-BOUNDARY-004
BUSDM-GAINERS-PARAM-001
BUSDM-GAINERS-PARAM-002
BUSDM-GAINERS-PARAM-003
BUSDM-GAINERS-PARAM-004
BUSDM-GAINERS-RESPONSE-001
BUSDM-GAINERS-DQC-001
BUSDM-GAINERS-LOGIC-001
BUSDM-GAINERS-PERF-001
```

Each success case must directly assert:

```python
assert response.status_code == 200
body = response.json()
assert str(body["code"]) == "200"
assert body["status"] == "success"
assert body["message"]
data = body["data"]
assert "change_threshold" in data
assert "days_history" in data
assert "count" in data
assert "sort_by" in data
assert "items" in data
assert isinstance(data["items"], list)
assert data["count"] == len(data["items"])
```

- [ ] **Step 4: Verify collection and markers**

Run:

```bash
python3 -m pytest tests/test_binance_usdm_api.py --collect-only -q
```

Expected:

```text
28 tests collected
```

- [ ] **Step 5: Run BUSDM tests**

Run:

```bash
python3 -m pytest tests/test_binance_usdm_api.py -v
```

Expected: 28 passed, or 28 skipped when `settings.base_url` is not configured for live API tests.

- [ ] **Step 6: Commit BUSDM rewrite**

Run:

```bash
git add tests/test_binance_usdm_api.py
git commit -m "test: rewrite BUSDM API cases as independent tests"
```

Expected: commit succeeds with only `tests/test_binance_usdm_api.py` staged.

---

### Task 4: Rewrite `test_factor_data_api.py`

**Files:**
- Modify: `tests/test_factor_data_api.py`
- Depends on: `api_services/factor_data_api.py`

- [ ] **Step 1: Replace imports, fixture, constants, and request body factory**

Use this upper-layer shape:

```python
from __future__ import annotations

from decimal import Decimal

import allure
import pytest
from requests.exceptions import HTTPError

from api_services.factor_data_api import FactorDataAPI
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
```

- [ ] **Step 2: Implement every FD Case ID as one function**

Implement independent functions for:

```text
FD-DATASET-KLINE-FUTURE-NORMAL-001
FD-DATASET-KLINE-SPOT-NORMAL-001
FD-DATASET-BINANCE-FUNDING-NORMAL-001
FD-DATASET-OI-NORMAL-001
FD-DATASET-LS-NORMAL-001
FD-DATASET-TAKER-NORMAL-001
FD-DATASET-PARAM-001
FD-DATASET-BOUNDARY-001
FD-SYMBOLS-NORMAL-001
FD-SYMBOLS-NORMAL-002
FD-SYMBOLS-PARAM-001
FD-SYMBOLS-PARAM-002
FD-SYMBOLS-PARAM-003
FD-SYMBOLS-PARAM-004
FD-TIME-NORMAL-001
FD-TIME-BOUNDARY-001
FD-TIME-PARAM-001
FD-TIME-PARAM-002
FD-TIME-PARAM-003
FD-FIELDS-NORMAL-001
FD-FIELDS-BOUNDARY-001
FD-FIELDS-PARAM-001
FD-QUALITY-NORMAL-001
FD-QUALITY-BOUNDARY-001
FD-QUALITY-PARAM-001
FD-PAGE-BOUNDARY-001
FD-PAGE-BOUNDARY-002
FD-PAGE-PARAM-001
FD-PAGE-PARAM-002
FD-CURSOR-NORMAL-001
FD-CURSOR-PARAM-001
FD-SORT-NORMAL-001
FD-SORT-NORMAL-002
FD-SORT-PARAM-001
```

Every successful FD case must directly assert:

```python
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
assert "query" in data
assert "rows" in data
assert "next_cursor" in data
assert "has_more" in data
assert "row_count_returned" in data
assert isinstance(data["rows"], list)
assert isinstance(data["has_more"], bool)
assert data["row_count_returned"] == len(data["rows"])
```

For `FD-CURSOR-NORMAL-001`, the case itself performs the second request:

```python
response = factor_data_api.query(
    dataset="kline_data_future",
    symbols=SYMBOLS_SINGLE,
    interval=INTERVAL_1M,
    start_time_ms=START_TIME_MS,
    end_time_ms=END_TIME_MS,
    page_size=PAGE_SIZE_SMALL,
    sort="asc",
)
assert response.status_code == 200
body = response.json()
data = body["data"]
assert "next_cursor" in data
assert "has_more" in data
if data["has_more"] and data["next_cursor"]:
    second_response = factor_data_api.query(
        dataset="kline_data_future",
        symbols=SYMBOLS_SINGLE,
        interval=INTERVAL_1M,
        start_time_ms=START_TIME_MS,
        end_time_ms=END_TIME_MS,
        page_size=PAGE_SIZE_SMALL,
        cursor=data["next_cursor"],
        sort="asc",
    )
    assert second_response.status_code == 200
    second_body = second_response.json()
    assert str(second_body["code"]) == "200"
    assert second_body["status"] == "success"
    first_rows = {str(row) for row in data["rows"]}
    second_rows = {str(row) for row in second_body["data"]["rows"]}
    assert first_rows.isdisjoint(second_rows)
```

For `FD-SORT-NORMAL-001` and `FD-SORT-NORMAL-002`, sorting assertions are allowed because `sort` is explicit:

```python
timestamps = []
for row in data["rows"]:
    if row.get("timestamp") is not None:
        timestamps.append(int(row["timestamp"]))
if len(timestamps) > 1:
    assert timestamps == sorted(timestamps)
```

Use `sorted(timestamps, reverse=True)` only in `FD-SORT-NORMAL-002`.

- [ ] **Step 3: Verify collection**

Run:

```bash
python3 -m pytest tests/test_factor_data_api.py --collect-only -q
```

Expected:

```text
34 tests collected
```

- [ ] **Step 4: Run Factor Data tests**

Run:

```bash
python3 -m pytest tests/test_factor_data_api.py -v
```

Expected: 34 passed, or 34 skipped when `settings.base_url` is not configured for live API tests.

- [ ] **Step 5: Commit FD rewrite**

Run:

```bash
git add tests/test_factor_data_api.py
git commit -m "test: rewrite factor data API cases as independent tests"
```

Expected: commit succeeds with only `tests/test_factor_data_api.py` staged.

---

### Task 5: Rewrite `test_coinglass_api.py`

**Files:**
- Modify: `tests/test_coinglass_api.py`
- Depends on: `api_services/coinglass_api.py`

- [ ] **Step 1: Replace imports, fixture, and constants**

Use this upper-layer shape:

```python
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
```

- [ ] **Step 2: Implement every CG Case ID as one function**

Implement independent functions for:

```text
CG-FR-OHLC-NORMAL-001
CG-FR-OHLC-BOUNDARY-001
CG-FR-OHLC-BOUNDARY-002
CG-FR-OHLC-PARAM-001
CG-FR-OHLC-PARAM-002
CG-FR-OHLC-PARAM-003
CG-FR-OHLC-RESPONSE-001
CG-FR-OHLC-DQC-001
CG-FR-OHLC-LOGIC-001
CG-FR-EXCHANGE-NORMAL-001
CG-FR-EXCHANGE-BOUNDARY-001
CG-FR-EXCHANGE-BOUNDARY-002
CG-FR-EXCHANGE-PARAM-001
CG-FR-EXCHANGE-PARAM-002
CG-FR-EXCHANGE-RESPONSE-001
CG-FR-EXCHANGE-DQC-001
CG-FR-ARB-NORMAL-001
CG-FR-ARB-BOUNDARY-001
CG-FR-ARB-PARAM-001
CG-FR-ARB-PARAM-002
CG-FR-ARB-RESPONSE-001
CG-FR-ARB-DQC-001
CG-FR-SUMMARY-NORMAL-001
CG-FR-SUMMARY-BOUNDARY-001
CG-FR-SUMMARY-PARAM-001
CG-FR-SUMMARY-PARAM-002
CG-FR-SUMMARY-RESPONSE-001
CG-FR-SUMMARY-DQC-001
CG-LS-HISTORY-NORMAL-001
CG-LS-HISTORY-NORMAL-002
CG-LS-HISTORY-BOUNDARY-001
CG-LS-HISTORY-BOUNDARY-002
CG-LS-HISTORY-PARAM-001
CG-LS-HISTORY-PARAM-002
CG-LS-HISTORY-PARAM-003
CG-LS-HISTORY-RESPONSE-001
CG-LS-HISTORY-DQC-001
CG-LS-HISTORY-LOGIC-001
CG-CONTROLLED-SUMMARY-NORMAL-001
CG-CONTROLLED-SUMMARY-BOUNDARY-001
CG-CONTROLLED-SUMMARY-PARAM-001
CG-CONTROLLED-SUMMARY-PARAM-002
CG-CONTROLLED-SUMMARY-RESPONSE-001
CG-CONTROLLED-SUMMARY-DQC-001
```

Every success case must directly assert:

```python
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
```

Funding-rate OHLC cases must assert item numeric fields and OHLC relation inside the case:

```python
data = body["data"]
assert isinstance(data, dict)
assert "symbol" in data
assert "timestamp" in data
assert "data" in data
rows = data["data"] or []
assert isinstance(rows, list)
for point in rows:
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
```

- [ ] **Step 3: Verify collection**

Run:

```bash
python3 -m pytest tests/test_coinglass_api.py --collect-only -q
```

Expected:

```text
44 tests collected
```

- [ ] **Step 4: Run CoinGlass tests**

Run:

```bash
python3 -m pytest tests/test_coinglass_api.py -v
```

Expected: 44 passed, or 44 skipped when `settings.base_url` is not configured for live API tests.

- [ ] **Step 5: Commit CG rewrite**

Run:

```bash
git add tests/test_coinglass_api.py
git commit -m "test: rewrite CoinGlass API cases as independent tests"
```

Expected: commit succeeds with only `tests/test_coinglass_api.py` staged.

---

### Task 6: Rewrite `test_open_interest_api.py`

**Files:**
- Modify: `tests/test_open_interest_api.py`
- Depends on: `api_services/open_interest_api.py`

- [ ] **Step 1: Replace imports, fixture, and constants**

Use this upper-layer shape:

```python
from __future__ import annotations

from decimal import Decimal

import allure
import pytest
from requests.exceptions import HTTPError

from api_services.open_interest_api import OpenInterestAPI
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
```

- [ ] **Step 2: Implement every OI Case ID as one function**

Implement independent functions for:

```text
OI-HISTORY-NORMAL-001
OI-HISTORY-NORMAL-002
OI-HISTORY-BOUNDARY-001
OI-HISTORY-BOUNDARY-002
OI-HISTORY-BOUNDARY-003
OI-HISTORY-PARAM-001
OI-HISTORY-PARAM-002
OI-HISTORY-PARAM-003
OI-HISTORY-RESPONSE-001
OI-HISTORY-DQC-001
OI-HISTORY-LOGIC-001
OI-AGG-HISTORY-NORMAL-001
OI-AGG-HISTORY-NORMAL-002
OI-AGG-HISTORY-BOUNDARY-001
OI-AGG-HISTORY-PARAM-001
OI-AGG-HISTORY-PARAM-002
OI-AGG-HISTORY-PARAM-003
OI-AGG-HISTORY-DQC-001
OI-AGG-HISTORY-LOGIC-001
OI-EXCHANGES-NORMAL-001
OI-EXCHANGES-BOUNDARY-001
OI-EXCHANGES-BOUNDARY-002
OI-EXCHANGES-PARAM-001
OI-EXCHANGES-PARAM-002
OI-EXCHANGES-RESPONSE-001
OI-EXCHANGES-DQC-001
OI-ORDERBOOK-NORMAL-001
OI-ORDERBOOK-NORMAL-002
OI-ORDERBOOK-BOUNDARY-001
OI-ORDERBOOK-BOUNDARY-002
OI-ORDERBOOK-PARAM-001
OI-ORDERBOOK-PARAM-002
OI-ORDERBOOK-PARAM-003
OI-ORDERBOOK-RESPONSE-001
OI-ORDERBOOK-DQC-001
OI-SUMMARY-NORMAL-001
OI-SUMMARY-BOUNDARY-001
OI-SUMMARY-BOUNDARY-002
OI-SUMMARY-PARAM-001
OI-SUMMARY-PARAM-002
OI-SUMMARY-RESPONSE-001
OI-SUMMARY-DQC-001
```

Every success case must directly assert the envelope:

```python
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
```

History and aggregated-history DQC cases must assert timestamp and numeric values inside the case:

```python
data = body["data"]
rows = data.get("data") if isinstance(data, dict) else data
if rows is None:
    rows = []
if isinstance(rows, dict):
    rows = [rows]
assert isinstance(rows, list)
for row in rows:
    time_value = row.get("time", row.get("timestamp", row.get("start_time")))
    if time_value is not None and str(time_value).isdigit():
        millis = int(time_value)
        assert len(str(millis)) == 13
    for field_name, value in row.items():
        lowered = field_name.lower()
        if any(token in lowered for token in ("interest", "quantity", "usd", "amount", "open", "high", "low", "close")):
            if value is not None and isinstance(value, (int, float, str)):
                assert Decimal(str(value)) is not None
```

Do not assert timestamp sorting unless the doc adds an explicit sort/order requirement.

- [ ] **Step 3: Verify collection**

Run:

```bash
python3 -m pytest tests/test_open_interest_api.py --collect-only -q
```

Expected:

```text
42 tests collected
```

- [ ] **Step 4: Run Open Interest tests**

Run:

```bash
python3 -m pytest tests/test_open_interest_api.py -v
```

Expected: 42 passed, or 42 skipped when `settings.base_url` is not configured for live API tests.

- [ ] **Step 5: Commit OI rewrite**

Run:

```bash
git add tests/test_open_interest_api.py
git commit -m "test: rewrite open interest API cases as independent tests"
```

Expected: commit succeeds with only `tests/test_open_interest_api.py` staged.

---

### Task 7: Rewrite `test_binance_full_api.py`

**Files:**
- Modify: `tests/test_binance_full_api.py`
- Depends on: `api_services/binance_full_api.py`

- [ ] **Step 1: Replace imports, fixture, and constants**

Use this upper-layer shape:

```python
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
```

- [ ] **Step 2: Implement every BF Case ID as one function**

Implement independent functions for all 84 BF IDs in `docs/test_cases_binance_full.md`.

Required groups:

```text
BF-META-TABLES: 3 cases
BF-REGISTRY: 6 cases
BF-COMPLETE: 6 cases
BF-DELISTED: 5 cases
BF-USDM-TIMERANGE: 5 cases
BF-COINM-PERP-TIMERANGE: 5 cases
BF-COINM-DELIVERY-TIMERANGE: 4 cases
BF-USDM-DELIVERY-TIMERANGE: 4 cases
BF-USDM-KLINE: 9 cases
BF-USDM-1H-ALL: 8 cases
BF-DERIV-KLINE: 10 cases
BF-FUNDING: 10 cases
BF-BATCH-BOUNDS: 9 cases
```

Every BF success case must directly assert:

```python
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
```

Kline cases must inline item-level assertions:

```python
data = body["data"]
assert "items" in data
assert isinstance(data["items"], list)
if "pagination" in data:
    assert "limit" in data["pagination"]
for item in data["items"]:
    for field in ("timestamp", "open", "high", "low", "close", "volume"):
        assert field in item
    timestamp = int(item["timestamp"])
    assert len(str(timestamp)) == 13
    for numeric_field in ("open", "high", "low", "close", "volume", "quote_volume"):
        if numeric_field in item and item[numeric_field] is not None:
            assert Decimal(str(item[numeric_field])) is not None
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
```

For `BF-USDM-1H-ALL-LOGIC-001`, sort assertion is allowed only because the request uses `order=time_asc`:

```python
timestamps = []
for item in data["items"]:
    timestamps.append(int(item["timestamp"]))
if len(timestamps) > 1:
    assert timestamps == sorted(timestamps)
```

For `BF-USDM-1H-ALL-BOUNDARY-001`, use `order=time_desc` and assert descending only if rows are returned:

```python
timestamps = []
for item in data["items"]:
    timestamps.append(int(item["timestamp"]))
if len(timestamps) > 1:
    assert timestamps == sorted(timestamps, reverse=True)
```

Multi-endpoint cases must be one test function per Case ID and loop over the documented endpoints inside that function:

```python
for response in (
    binance_full_api.get_coinm_perp_kline(...),
    binance_full_api.get_coinm_delivery_kline(...),
    binance_full_api.get_usdm_delivery_kline(...),
):
    assert response.status_code == 200
    body = response.json()
    assert str(body["code"]) == "200"
    assert body["status"] == "success"
    assert "data" in body
```

- [ ] **Step 3: Verify collection**

Run:

```bash
python3 -m pytest tests/test_binance_full_api.py --collect-only -q
```

Expected:

```text
84 tests collected
```

- [ ] **Step 4: Run Binance Full tests**

Run:

```bash
python3 -m pytest tests/test_binance_full_api.py -v
```

Expected: 84 passed, or 84 skipped when `settings.base_url` is not configured for live API tests.

- [ ] **Step 5: Commit BF rewrite**

Run:

```bash
git add tests/test_binance_full_api.py
git commit -m "test: rewrite Binance Full API cases as independent tests"
```

Expected: commit succeeds with only `tests/test_binance_full_api.py` staged.

---

### Task 8: Final Cross-Doc Verification

**Files:**
- Read: `docs/test_cases_binance_full.md`
- Read: `docs/test_cases_coinglass.md`
- Read: `docs/test_cases_factor_data.md`
- Read: `docs/test_cases_binance_usdm.md`
- Read: `docs/test_cases_open_interest.md`
- Read: rewritten test files

- [ ] **Step 1: Confirm there is no runtime docs parsing or raw requests**

Run:

```bash
grep -R "def _load_case_metadata\\|CASE_METADATA\\|_apply_case_allure\\|pytest.mark.parametrize\\|import requests\\|requests\\." -n tests/test_binance_full_api.py tests/test_coinglass_api.py tests/test_factor_data_api.py tests/test_binance_usdm_api.py tests/test_open_interest_api.py
```

Expected: no matches.

- [ ] **Step 2: Confirm docs Case IDs match test Case IDs**

Run:

```bash
python3 -c 'from pathlib import Path
import re
pairs=[("BF","docs/test_cases_binance_full.md","tests/test_binance_full_api.py"),("CG","docs/test_cases_coinglass.md","tests/test_coinglass_api.py"),("FD","docs/test_cases_factor_data.md","tests/test_factor_data_api.py"),("BUSDM","docs/test_cases_binance_usdm.md","tests/test_binance_usdm_api.py"),("OI","docs/test_cases_open_interest.md","tests/test_open_interest_api.py")]
for prefix, doc, test in pairs:
    doc_text=Path(doc).read_text(encoding="utf-8")
    test_text=Path(test).read_text(encoding="utf-8")
    doc_ids=[]
    for line in doc_text.splitlines():
        if line.startswith(f"| {prefix}-"):
            cells=[cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
            doc_ids.append(cells[0])
    test_ids=sorted(set(re.findall(rf"{prefix}-[A-Z0-9-]+", test_text)))
    missing=sorted(set(doc_ids)-set(test_ids))
    extra=sorted(set(test_ids)-set(doc_ids))
    print(prefix, "doc", len(set(doc_ids)), "test", len(set(test_ids)), "missing", len(missing), "extra", len(extra))
    if missing:
        print("missing:", ", ".join(missing))
    if extra:
        print("extra:", ", ".join(extra))'
```

Expected:

```text
BF doc 84 test 84 missing 0 extra 0
CG doc 44 test 44 missing 0 extra 0
FD doc 34 test 34 missing 0 extra 0
BUSDM doc 28 test 28 missing 0 extra 0
OI doc 42 test 42 missing 0 extra 0
```

- [ ] **Step 3: Confirm pytest collection totals**

Run:

```bash
python3 -m pytest tests/test_binance_full_api.py tests/test_coinglass_api.py tests/test_factor_data_api.py tests/test_binance_usdm_api.py tests/test_open_interest_api.py --collect-only -q
```

Expected:

```text
232 tests collected
```

- [ ] **Step 4: Run all redesigned tests**

Run:

```bash
python3 -m pytest tests/test_binance_full_api.py tests/test_coinglass_api.py tests/test_factor_data_api.py tests/test_binance_usdm_api.py tests/test_open_interest_api.py -v
```

Expected: 232 passed, or 232 skipped when `settings.base_url` is not configured for live API tests.

- [ ] **Step 5: Commit final verification cleanup**

Run:

```bash
git status --short
```

Expected: only intentional files are modified or newly created.

Run:

```bash
git add pytest.ini api_services/binance_full_api.py api_services/coinglass_api.py api_services/factor_data_api.py api_services/binance_usdm_api.py api_services/open_interest_api.py tests/test_binance_full_api.py tests/test_coinglass_api.py tests/test_factor_data_api.py tests/test_binance_usdm_api.py tests/test_open_interest_api.py
git commit -m "test: align platform API tests with kline design"
```

Expected: commit succeeds when earlier task commits were not used. If earlier task commits were used, this step should report no changes to commit.

---

## Self-Review Checklist

- Spec coverage:
  - Case independence is enforced by one function per Case ID.
  - `test_kline_api.py` remains untouched.
  - All five non-kline files are covered.
  - Service-layer wrappers remove direct `requests` calls from tests.
  - Static Allure titles and docstrings replace dynamic docs metadata.
  - Per-case inline assertions replace shared assertion helpers.
  - Sorting assertions are restricted to documented `sort` or `order` cases.
- Placeholder scan:
  - Plan uses concrete files, commands, expected outputs, service method names, and Case ID lists.
- Type consistency:
  - Service fixtures return the class imported by the test file.
  - `start_time_ms`/`end_time_ms` are used for platform endpoints that document milliseconds.
  - `start_time`/`end_time` are used for Coinglass and Open Interest endpoints that document those names.
  - `symbol` is used for USDM/Coinglass where documented.
  - `pair` and `contract_type` are used for Coin-M and delivery Binance Full endpoints.
