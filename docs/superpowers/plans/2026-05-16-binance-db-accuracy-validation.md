# Binance DB Accuracy Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a manually triggered pytest/Allure validation suite that exhaustively compares every Binance raw/metadata database row covered by the database PDF against the corresponding Binance REST source.

**Architecture:** Pytest remains the only user-facing entry point, but it delegates to focused helper modules under `tests/db_accuracy/`: table-spec loading, DB scanning, Binance source fetching, strict field comparison, and result aggregation. The suite is skipped by default unless `--run-db-accuracy` is passed, does not write to the business database, and reports every table/key/window mismatch after finishing the whole run.

**Tech Stack:** Python 3.12, pytest, allure-pytest, PyYAML, PyMySQL through existing `core.db_client.DBClient`, existing Binance market API wrappers, existing `core.http_client.HTTPClient`, Decimal for strict numeric normalization.

---

## Confirmed Scope

- Include only Binance raw/metadata tables documented by `docs/数据库/数据库表及解释 .pdf` and `docs/接口级字段矩阵.md`.
- Include:
  - `kline_data_future_raw`
  - `kline_data_spot_raw`
  - `binance_usdm_funding_rate_raw`
  - `binance_kline_all_future_raw`
  - `binance_funding_rate_all_future_raw`
  - `binance_kline_all_future_raw_1h`
  - `binance_1h_usdm_kline_raw`
  - `binance_1h_usdm_funding_rate_raw`
  - `binance_kline_coinm_perp_raw`
  - `binance_kline_coinm_delivery_raw`
  - `binance_funding_rate_coinm_perp_raw`
  - `binance_kline_usdm_delivery_raw`
  - `binance_futures_symbols`
- Exclude clean/curated tables, CoinGlass tables, DQC tables, accuracy bookkeeping tables, repair tables, and application-only derived tables.
- Each run starts from scratch and writes only pytest/Allure artifacts.
- The run continues after mismatches and fails at the end with an aggregated summary.
- Strict comparison means normalized string/Decimal equality. There is no numeric tolerance in v1.
- Recent unstable data is skipped by a configurable safety window. Default is 24 hours.
- The suite is not part of default CI. It runs only with `--run-db-accuracy`.

## File Structure

- Create: `data/binance_db_accuracy_tables.yaml`
  - Declarative table-to-source mapping, fields, key fields, time fields, and request-window rules.
- Create: `tests/db_accuracy/__init__.py`
  - Marks the helper directory as an importable package.
- Create: `tests/db_accuracy/models.py`
  - Dataclasses for table specs, resolved specs, source rows, validation differences, and run summaries.
- Create: `tests/db_accuracy/table_specs.py`
  - YAML loader and schema resolver that checks configured table fields against actual DB columns.
- Create: `tests/db_accuracy/db_reader.py`
  - Read-only DB scanner that discovers columns, key ranges, and rows per validation window.
- Create: `tests/db_accuracy/binance_source.py`
  - Binance REST source adapter using existing `api_services/binance/*` wrappers.
- Create: `tests/db_accuracy/compare.py`
  - Strict normalization and row comparison logic.
- Create: `tests/db_accuracy/runner.py`
  - Orchestrates all specs, keys, windows, source fetches, comparisons, and summary generation.
- Create: `tests/test_db_accuracy_config.py`
  - Unit tests for table spec loading and resolution.
- Create: `tests/test_db_accuracy_compare.py`
  - Unit tests for strict Decimal/string comparison and mismatch reporting.
- Create: `tests/test_db_accuracy_runner.py`
  - Unit tests for aggregation behavior and "continue through failures".
- Create: `tests/test_binance_db_accuracy.py`
  - Manual pytest entry point for the full DB accuracy run.
- Modify: `tests/conftest.py`
  - Add `--run-db-accuracy`, safety-window CLI options, and default skip behavior for `@pytest.mark.db_accuracy`.
- Modify: `pytest.ini`
  - Register `db_accuracy` marker.
- Create: `docs/binance_db_accuracy_validation.md`
  - Operator docs for running the suite and reading the report.

---

### Task 1: Add Manual Pytest Gate

**Files:**
- Modify: `pytest.ini`
- Modify: `tests/conftest.py`
- Test: `tests/test_binance_db_accuracy.py`

- [ ] **Step 1: Write a failing marker/gate test file**

Create `tests/test_binance_db_accuracy.py` with this minimal content:

```python
import pytest


pytestmark = pytest.mark.db_accuracy


def test_binance_db_accuracy_gate_smoke():
    """
    Case ID: DB-ACC-GATE-SMOKE
    测试目的: 验证 db_accuracy 标记用例默认由显式开关控制。
    """
    assert True
```

- [ ] **Step 2: Run collection to verify the marker is not registered**

Run:

```bash
python3 -m pytest tests/test_binance_db_accuracy.py --collect-only -q
```

Expected: pytest emits `PytestUnknownMarkWarning: Unknown pytest.mark.db_accuracy`.

- [ ] **Step 3: Register the marker**

Add this line under `markers =` in `pytest.ini`:

```ini
    db_accuracy: Run manual Binance database-to-source accuracy validation
```

- [ ] **Step 4: Add explicit run options and default skip behavior**

Modify `tests/conftest.py` as follows.

Add these options inside `pytest_addoption` after the existing `--env` option:

```python
    parser.addoption(
        "--run-db-accuracy",
        action="store_true",
        default=False,
        help="Run manual database accuracy validation tests.",
    )
    parser.addoption(
        "--db-accuracy-safety-hours",
        action="store",
        type=int,
        default=24,
        help="Skip source rows newer than this many hours during DB accuracy validation.",
    )
    parser.addoption(
        "--db-accuracy-table",
        action="append",
        default=[],
        help="Limit DB accuracy validation to one configured table. Can be passed multiple times.",
    )
```

Extend `ALLURE_MARKER_FEATURES` with:

```python
    "db_accuracy": "Database Source Accuracy",
```

Append this block at the end of `pytest_collection_modifyitems`:

```python
    if config.getoption("--run-db-accuracy"):
        return

    skip_db_accuracy = pytest.mark.skip(
        reason="database accuracy validation requires --run-db-accuracy"
    )
    for item in items:
        if "db_accuracy" in item.keywords:
            item.add_marker(skip_db_accuracy)
```

- [ ] **Step 5: Verify default run skips the manual test**

Run:

```bash
python3 -m pytest tests/test_binance_db_accuracy.py -q
```

Expected: `1 skipped`.

- [ ] **Step 6: Verify explicit run executes the manual test**

Run:

```bash
python3 -m pytest tests/test_binance_db_accuracy.py -q --run-db-accuracy
```

Expected: `1 passed`.

- [ ] **Step 7: Commit the pytest gate**

Run:

```bash
git add pytest.ini tests/conftest.py tests/test_binance_db_accuracy.py
git commit -m "test: gate manual DB accuracy validation"
```

Expected: commit succeeds with only the marker, conftest gate, and smoke test staged.

---

### Task 2: Add Declarative Binance Table Specs

**Files:**
- Create: `data/binance_db_accuracy_tables.yaml`
- Create: `tests/db_accuracy/__init__.py`
- Create: `tests/db_accuracy/models.py`
- Create: `tests/db_accuracy/table_specs.py`
- Test: `tests/test_db_accuracy_config.py`

- [ ] **Step 1: Write failing config-loader tests**

Create `tests/test_db_accuracy_config.py`:

```python
from tests.db_accuracy.table_specs import load_table_specs


def test_load_table_specs_includes_expected_binance_tables():
    specs = load_table_specs()
    names = {spec.table for spec in specs}

    assert "kline_data_future_raw" in names
    assert "kline_data_spot_raw" in names
    assert "binance_kline_all_future_raw" in names
    assert "binance_funding_rate_all_future_raw" in names
    assert "binance_kline_coinm_perp_raw" in names
    assert "binance_kline_coinm_delivery_raw" in names
    assert "binance_kline_usdm_delivery_raw" in names
    assert "binance_futures_symbols" in names


def test_loaded_specs_have_key_and_compare_fields():
    specs = load_table_specs()

    for spec in specs:
        assert spec.table
        assert spec.kind in {"kline", "funding", "registry"}
        assert spec.endpoint
        if spec.kind != "registry":
            assert spec.key_fields
            assert spec.time_fields
            assert spec.compare_fields
        if spec.kind == "registry":
            assert spec.key_fields == ("symbol",)
            assert "symbol" in spec.compare_fields
```

- [ ] **Step 2: Run tests to verify imports fail before implementation**

Run:

```bash
python3 -m pytest tests/test_db_accuracy_config.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'tests.db_accuracy'`.

- [ ] **Step 3: Create the helper package marker**

Create `tests/db_accuracy/__init__.py`:

```python
"""Helpers for manual database-to-source accuracy validation tests."""
```

- [ ] **Step 4: Create dataclasses for table specs**

Create `tests/db_accuracy/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TableSpec:
    table: str
    kind: str
    endpoint: str
    key_fields: tuple[str, ...]
    time_fields: tuple[str, ...]
    interval_field: str | None
    compare_fields: tuple[str, ...]
    request_limit: int
    fixed_interval: str | None = None
    contract_type_field: str | None = None
    pair_field: str | None = None
    symbol_field: str | None = "symbol"
    source_time_field: str | None = None


@dataclass(frozen=True)
class ResolvedTableSpec:
    spec: TableSpec
    columns: tuple[str, ...]
    time_field: str | None
    interval_field: str | None
    compare_fields: tuple[str, ...]
    key_fields: tuple[str, ...]


@dataclass(frozen=True)
class ValidationKey:
    values: dict[str, Any]

    def label(self) -> str:
        return ",".join(f"{key}={self.values[key]}" for key in sorted(self.values))


@dataclass(frozen=True)
class ValidationWindow:
    table: str
    key: ValidationKey
    start_ms: int | None
    end_ms: int | None


@dataclass(frozen=True)
class SourceRow:
    key: Any
    fields: dict[str, Any]


@dataclass(frozen=True)
class Difference:
    table: str
    key_label: str
    row_key: Any
    field: str
    db_value: Any
    source_value: Any
    reason: str


@dataclass
class TableRunResult:
    table: str
    windows_checked: int = 0
    db_rows_checked: int = 0
    source_rows_checked: int = 0
    differences: list[Difference] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.differences


@dataclass
class AccuracyRunResult:
    tables: list[TableRunResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(table.passed for table in self.tables)

    def summary_text(self) -> str:
        table_count = len(self.tables)
        diff_count = sum(len(table.differences) for table in self.tables)
        row_count = sum(table.db_rows_checked for table in self.tables)
        window_count = sum(table.windows_checked for table in self.tables)
        lines = [
            f"tables={table_count}",
            f"windows_checked={window_count}",
            f"db_rows_checked={row_count}",
            f"differences={diff_count}",
        ]
        for table in self.tables:
            lines.append(
                f"{table.table}: windows={table.windows_checked}, "
                f"db_rows={table.db_rows_checked}, "
                f"source_rows={table.source_rows_checked}, "
                f"differences={len(table.differences)}"
            )
        return "\n".join(lines)
```

- [ ] **Step 5: Add the YAML table map**

Create `data/binance_db_accuracy_tables.yaml`:

```yaml
tables:
  - table: kline_data_future_raw
    kind: kline
    endpoint: usdm_klines
    key_fields: [symbol, interval]
    time_fields: [timestamp, open_time]
    interval_field: interval
    compare_fields: [timestamp, open_time, open, high, low, close, volume, close_time, quote_volume, trade_count, trades, taker_buy_base_volume, taker_buy_quote_volume]
    request_limit: 1000

  - table: kline_data_spot_raw
    kind: kline
    endpoint: spot_klines
    key_fields: [symbol, interval]
    time_fields: [timestamp, open_time]
    interval_field: interval
    compare_fields: [timestamp, open_time, open, high, low, close, volume, close_time, quote_volume, trade_count, trades, taker_buy_base_volume, taker_buy_quote_volume]
    request_limit: 1000

  - table: binance_usdm_funding_rate_raw
    kind: funding
    endpoint: usdm_funding
    key_fields: [symbol]
    time_fields: [funding_time, timestamp]
    interval_field:
    compare_fields: [symbol, funding_rate, funding_time, mark_price]
    request_limit: 1000

  - table: binance_kline_all_future_raw
    kind: kline
    endpoint: usdm_klines
    key_fields: [symbol, interval]
    time_fields: [timestamp, open_time]
    interval_field: interval
    compare_fields: [timestamp, open_time, open, high, low, close, volume, close_time, quote_volume, trade_count, trades, taker_buy_base_volume, taker_buy_quote_volume]
    request_limit: 1000

  - table: binance_funding_rate_all_future_raw
    kind: funding
    endpoint: usdm_funding
    key_fields: [symbol]
    time_fields: [funding_time, timestamp]
    interval_field:
    compare_fields: [symbol, funding_rate, funding_time, mark_price]
    request_limit: 1000

  - table: binance_kline_all_future_raw_1h
    kind: kline
    endpoint: usdm_klines
    key_fields: [symbol]
    time_fields: [timestamp, open_time]
    interval_field:
    fixed_interval: 1h
    compare_fields: [timestamp, open_time, open, high, low, close, volume, close_time, quote_volume, trade_count, trades, taker_buy_base_volume, taker_buy_quote_volume]
    request_limit: 1000

  - table: binance_1h_usdm_kline_raw
    kind: kline
    endpoint: usdm_klines
    key_fields: [symbol]
    time_fields: [timestamp, open_time]
    interval_field:
    fixed_interval: 1h
    compare_fields: [timestamp, open_time, open, high, low, close, volume, close_time, quote_volume, trade_count, trades, taker_buy_base_volume, taker_buy_quote_volume]
    request_limit: 1000

  - table: binance_1h_usdm_funding_rate_raw
    kind: funding
    endpoint: usdm_funding
    key_fields: [symbol]
    time_fields: [funding_time, timestamp]
    interval_field:
    compare_fields: [symbol, funding_rate, funding_time, mark_price]
    request_limit: 1000

  - table: binance_kline_coinm_perp_raw
    kind: kline
    endpoint: coinm_klines
    key_fields: [symbol, interval]
    time_fields: [timestamp, open_time]
    interval_field: interval
    compare_fields: [timestamp, open_time, open, high, low, close, volume, close_time, quote_volume, trade_count, trades, taker_buy_base_volume, taker_buy_quote_volume]
    request_limit: 1000

  - table: binance_kline_coinm_delivery_raw
    kind: kline
    endpoint: coinm_continuous_klines
    key_fields: [pair, contract_type, interval]
    time_fields: [timestamp, open_time]
    interval_field: interval
    contract_type_field: contract_type
    pair_field: pair
    compare_fields: [timestamp, open_time, open, high, low, close, volume, close_time, quote_volume, trade_count, trades, taker_buy_base_volume, taker_buy_quote_volume]
    request_limit: 1000

  - table: binance_funding_rate_coinm_perp_raw
    kind: funding
    endpoint: coinm_funding
    key_fields: [symbol]
    time_fields: [funding_time, timestamp]
    interval_field:
    compare_fields: [symbol, funding_rate, funding_time]
    request_limit: 1000

  - table: binance_kline_usdm_delivery_raw
    kind: kline
    endpoint: usdm_continuous_klines
    key_fields: [pair, contract_type, interval]
    time_fields: [timestamp, open_time]
    interval_field: interval
    contract_type_field: contract_type
    pair_field: pair
    compare_fields: [timestamp, open_time, open, high, low, close, volume, close_time, quote_volume, trade_count, trades, taker_buy_base_volume, taker_buy_quote_volume]
    request_limit: 1000

  - table: binance_futures_symbols
    kind: registry
    endpoint: usdm_exchange_info
    key_fields: [symbol]
    time_fields: []
    interval_field:
    compare_fields: [symbol, status, contract_type, quote_asset, margin_asset, onboard_date_ms]
    request_limit: 1000
```

- [ ] **Step 6: Implement the YAML loader**

Create `tests/db_accuracy/table_specs.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from tests.db_accuracy.models import ResolvedTableSpec, TableSpec


SPEC_PATH = Path("data/binance_db_accuracy_tables.yaml")


def _as_tuple(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(str(item) for item in value)


def load_table_specs(path: Path = SPEC_PATH) -> list[TableSpec]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    specs: list[TableSpec] = []
    for item in raw["tables"]:
        specs.append(
            TableSpec(
                table=str(item["table"]),
                kind=str(item["kind"]),
                endpoint=str(item["endpoint"]),
                key_fields=_as_tuple(item.get("key_fields")),
                time_fields=_as_tuple(item.get("time_fields")),
                interval_field=item.get("interval_field"),
                compare_fields=_as_tuple(item.get("compare_fields")),
                request_limit=int(item.get("request_limit", 1000)),
                fixed_interval=item.get("fixed_interval"),
                contract_type_field=item.get("contract_type_field"),
                pair_field=item.get("pair_field"),
                symbol_field=item.get("symbol_field", "symbol"),
                source_time_field=item.get("source_time_field"),
            )
        )
    return specs


def resolve_spec(spec: TableSpec, columns: set[str]) -> ResolvedTableSpec:
    missing_key_fields = [field for field in spec.key_fields if field not in columns]
    if missing_key_fields:
        raise ValueError(f"{spec.table} missing key fields: {missing_key_fields}")

    time_field = None
    for candidate in spec.time_fields:
        if candidate in columns:
            time_field = candidate
            break

    if spec.kind != "registry" and time_field is None:
        raise ValueError(f"{spec.table} has no configured time field in DB columns")

    interval_field = spec.interval_field if spec.interval_field in columns else None
    if spec.interval_field and interval_field is None and not spec.fixed_interval:
        raise ValueError(f"{spec.table} missing interval field: {spec.interval_field}")

    compare_fields = tuple(field for field in spec.compare_fields if field in columns)
    if not compare_fields:
        raise ValueError(f"{spec.table} has no comparable fields present in DB columns")

    return ResolvedTableSpec(
        spec=spec,
        columns=tuple(sorted(columns)),
        time_field=time_field,
        interval_field=interval_field,
        compare_fields=compare_fields,
        key_fields=spec.key_fields,
    )
```

- [ ] **Step 7: Verify config tests pass**

Run:

```bash
python3 -m pytest tests/test_db_accuracy_config.py -q
```

Expected: `2 passed`.

- [ ] **Step 8: Commit table specs**

Run:

```bash
git add data/binance_db_accuracy_tables.yaml tests/db_accuracy/__init__.py tests/db_accuracy/models.py tests/db_accuracy/table_specs.py tests/test_db_accuracy_config.py
git commit -m "test: define Binance DB accuracy table specs"
```

Expected: commit succeeds with the spec files and tests staged.

---

### Task 3: Add Read-Only DB Scanner And Window Planner

**Files:**
- Create: `tests/db_accuracy/db_reader.py`
- Modify: `tests/db_accuracy/models.py`
- Test: `tests/test_db_accuracy_config.py`

- [ ] **Step 1: Add DB scanner unit tests with a fake DB client**

Append to `tests/test_db_accuracy_config.py`:

```python
from tests.db_accuracy.db_reader import DBAccuracyReader, interval_to_ms
from tests.db_accuracy.models import TableSpec
from tests.db_accuracy.table_specs import resolve_spec


class FakeDB:
    def __init__(self):
        self.queries = []

    def query(self, sql, params=None):
        self.queries.append((sql, params))
        if sql.startswith("SHOW COLUMNS FROM"):
            return [
                {"Field": "symbol"},
                {"Field": "interval"},
                {"Field": "timestamp"},
                {"Field": "open"},
                {"Field": "close"},
            ]
        if "GROUP BY" in sql:
            return [
                {
                    "symbol": "BTCUSDT",
                    "interval": "1h",
                    "min_time_ms": 1704067200000,
                    "max_time_ms": 1704070800000,
                }
            ]
        return []


def test_interval_to_ms_supports_binance_intervals():
    assert interval_to_ms("1m") == 60_000
    assert interval_to_ms("15m") == 900_000
    assert interval_to_ms("1h") == 3_600_000
    assert interval_to_ms("1d") == 86_400_000


def test_reader_builds_key_ranges_from_configured_fields():
    spec = TableSpec(
        table="sample_kline",
        kind="kline",
        endpoint="usdm_klines",
        key_fields=("symbol", "interval"),
        time_fields=("timestamp",),
        interval_field="interval",
        compare_fields=("timestamp", "open", "close"),
        request_limit=1000,
    )
    db = FakeDB()
    reader = DBAccuracyReader(db)
    resolved = resolve_spec(spec, reader.table_columns("sample_kline"))
    ranges = reader.key_ranges(resolved, stable_before_ms=1704074400000)

    assert len(ranges) == 1
    assert ranges[0].key.values == {"symbol": "BTCUSDT", "interval": "1h"}
    assert ranges[0].start_ms == 1704067200000
    assert ranges[0].end_ms == 1704070800000
```

- [ ] **Step 2: Run tests to verify scanner import fails**

Run:

```bash
python3 -m pytest tests/test_db_accuracy_config.py::test_interval_to_ms_supports_binance_intervals tests/test_db_accuracy_config.py::test_reader_builds_key_ranges_from_configured_fields -q
```

Expected: FAIL with `ModuleNotFoundError` or `ImportError` for `tests.db_accuracy.db_reader`.

- [ ] **Step 3: Add range model**

Append to `tests/db_accuracy/models.py`:

```python
@dataclass(frozen=True)
class KeyTimeRange:
    table: str
    key: ValidationKey
    start_ms: int
    end_ms: int
```

- [ ] **Step 4: Implement the DB scanner**

Create `tests/db_accuracy/db_reader.py`:

```python
from __future__ import annotations

import re
from typing import Any

from tests.db_accuracy.models import KeyTimeRange, ResolvedTableSpec, ValidationKey


IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def quote_identifier(identifier: str) -> str:
    if not IDENTIFIER_RE.match(identifier):
        raise ValueError(f"Unsafe SQL identifier: {identifier}")
    return f"`{identifier}`"


def interval_to_ms(interval: str) -> int:
    unit = interval[-1]
    amount = int(interval[:-1])
    multipliers = {
        "m": 60_000,
        "h": 3_600_000,
        "d": 86_400_000,
        "w": 604_800_000,
    }
    if unit not in multipliers:
        raise ValueError(f"Unsupported Binance interval: {interval}")
    return amount * multipliers[unit]


class DBAccuracyReader:
    def __init__(self, db_client):
        self.db = db_client

    def table_columns(self, table: str) -> set[str]:
        sql = f"SHOW COLUMNS FROM {quote_identifier(table)}"
        rows = self.db.query(sql)
        return {str(row["Field"]) for row in rows}

    def key_ranges(self, spec: ResolvedTableSpec, stable_before_ms: int) -> list[KeyTimeRange]:
        if spec.time_field is None:
            return []

        table_sql = quote_identifier(spec.spec.table)
        time_sql = quote_identifier(spec.time_field)
        key_sql = ", ".join(quote_identifier(field) for field in spec.key_fields)
        select_fields = ", ".join(quote_identifier(field) for field in spec.key_fields)
        sql = (
            f"SELECT {select_fields}, MIN({time_sql}) AS min_time_ms, "
            f"MAX({time_sql}) AS max_time_ms "
            f"FROM {table_sql} "
            f"WHERE {time_sql} < %s "
            f"GROUP BY {key_sql} "
            f"ORDER BY {key_sql}"
        )
        rows = self.db.query(sql, (stable_before_ms,))
        ranges: list[KeyTimeRange] = []
        for row in rows:
            if row["min_time_ms"] is None or row["max_time_ms"] is None:
                continue
            ranges.append(
                KeyTimeRange(
                    table=spec.spec.table,
                    key=ValidationKey({field: row[field] for field in spec.key_fields}),
                    start_ms=int(row["min_time_ms"]),
                    end_ms=int(row["max_time_ms"]),
                )
            )
        return ranges

    def rows_for_window(
        self,
        spec: ResolvedTableSpec,
        key: ValidationKey,
        start_ms: int,
        end_ms: int,
    ) -> list[dict[str, Any]]:
        if spec.time_field is None:
            return []

        table_sql = quote_identifier(spec.spec.table)
        time_sql = quote_identifier(spec.time_field)
        select_fields = list(dict.fromkeys([*spec.key_fields, spec.time_field, *spec.compare_fields]))
        select_sql = ", ".join(quote_identifier(field) for field in select_fields)
        where_parts = [f"{time_sql} >= %s", f"{time_sql} <= %s"]
        params: list[Any] = [start_ms, end_ms]
        for field in spec.key_fields:
            where_parts.append(f"{quote_identifier(field)} = %s")
            params.append(key.values[field])

        sql = (
            f"SELECT {select_sql} FROM {table_sql} "
            f"WHERE {' AND '.join(where_parts)} "
            f"ORDER BY {time_sql} ASC"
        )
        return list(self.db.query(sql, tuple(params)))

    def registry_rows(self, spec: ResolvedTableSpec) -> list[dict[str, Any]]:
        table_sql = quote_identifier(spec.spec.table)
        select_fields = list(dict.fromkeys([*spec.key_fields, *spec.compare_fields]))
        select_sql = ", ".join(quote_identifier(field) for field in select_fields)
        key_sql = ", ".join(quote_identifier(field) for field in spec.key_fields)
        sql = f"SELECT {select_sql} FROM {table_sql} ORDER BY {key_sql}"
        return list(self.db.query(sql))


def build_windows(
    spec: ResolvedTableSpec,
    time_range: KeyTimeRange,
) -> list[tuple[int, int]]:
    if spec.spec.kind == "funding":
        max_window_ms = 90 * 86_400_000
    else:
        interval = spec.spec.fixed_interval or str(time_range.key.values[spec.interval_field])
        max_window_ms = interval_to_ms(interval) * max(1, spec.spec.request_limit - 1)

    windows: list[tuple[int, int]] = []
    start_ms = time_range.start_ms
    while start_ms <= time_range.end_ms:
        end_ms = min(time_range.end_ms, start_ms + max_window_ms)
        windows.append((start_ms, end_ms))
        start_ms = end_ms + 1
    return windows
```

- [ ] **Step 5: Verify scanner tests pass**

Run:

```bash
python3 -m pytest tests/test_db_accuracy_config.py -q
```

Expected: all config/scanner tests pass.

- [ ] **Step 6: Commit DB scanner**

Run:

```bash
git add tests/db_accuracy/models.py tests/db_accuracy/db_reader.py tests/test_db_accuracy_config.py
git commit -m "test: add DB accuracy scanner"
```

Expected: commit succeeds with scanner files staged.

---

### Task 4: Add Strict Comparator

**Files:**
- Create: `tests/db_accuracy/compare.py`
- Test: `tests/test_db_accuracy_compare.py`

- [ ] **Step 1: Write failing comparator tests**

Create `tests/test_db_accuracy_compare.py`:

```python
from decimal import Decimal

from tests.db_accuracy.compare import compare_rows, normalize_value


def test_normalize_value_treats_numeric_strings_and_decimals_as_equal():
    assert normalize_value("1.2300") == normalize_value(Decimal("1.23"))
    assert normalize_value(1) == normalize_value("1.0")


def test_compare_rows_reports_field_difference():
    differences = compare_rows(
        table="kline_data_future_raw",
        key_label="symbol=BTCUSDT,interval=1h",
        row_key=1704067200000,
        db_row={"open": "100.00", "close": "101.00"},
        source_row={"open": "100.00", "close": "102.00"},
        fields=("open", "close"),
    )

    assert len(differences) == 1
    assert differences[0].field == "close"
    assert differences[0].reason == "value_mismatch"


def test_compare_rows_accepts_exact_normalized_match():
    differences = compare_rows(
        table="binance_funding_rate_all_future_raw",
        key_label="symbol=BTCUSDT",
        row_key=1704067200000,
        db_row={"funding_rate": "0.0100"},
        source_row={"funding_rate": Decimal("0.01")},
        fields=("funding_rate",),
    )

    assert differences == []
```

- [ ] **Step 2: Run tests to verify comparator import fails**

Run:

```bash
python3 -m pytest tests/test_db_accuracy_compare.py -q
```

Expected: FAIL with `ModuleNotFoundError` or `ImportError` for `tests.db_accuracy.compare`.

- [ ] **Step 3: Implement strict comparison**

Create `tests/db_accuracy/compare.py`:

```python
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from tests.db_accuracy.models import Difference


def normalize_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value.normalize()
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return Decimal(value).normalize()
    if isinstance(value, float):
        return Decimal(str(value)).normalize()

    text = str(value).strip()
    if text == "":
        return ""

    try:
        return Decimal(text).normalize()
    except InvalidOperation:
        return text


def compare_rows(
    table: str,
    key_label: str,
    row_key: Any,
    db_row: dict[str, Any],
    source_row: dict[str, Any],
    fields: tuple[str, ...],
) -> list[Difference]:
    differences: list[Difference] = []
    for field in fields:
        db_value = db_row.get(field)
        source_value = source_row.get(field)
        if normalize_value(db_value) != normalize_value(source_value):
            differences.append(
                Difference(
                    table=table,
                    key_label=key_label,
                    row_key=row_key,
                    field=field,
                    db_value=db_value,
                    source_value=source_value,
                    reason="value_mismatch",
                )
            )
    return differences
```

- [ ] **Step 4: Verify comparator tests pass**

Run:

```bash
python3 -m pytest tests/test_db_accuracy_compare.py -q
```

Expected: `3 passed`.

- [ ] **Step 5: Commit comparator**

Run:

```bash
git add tests/db_accuracy/compare.py tests/test_db_accuracy_compare.py
git commit -m "test: add strict DB accuracy comparator"
```

Expected: commit succeeds with comparator files staged.

---

### Task 5: Add Binance Source Adapter

**Files:**
- Create: `tests/db_accuracy/binance_source.py`
- Test: `tests/test_db_accuracy_runner.py`

- [ ] **Step 1: Write source adapter tests with fake API clients**

Create `tests/test_db_accuracy_runner.py` with the source adapter tests first:

```python
from tests.db_accuracy.binance_source import BinanceSource
from tests.db_accuracy.models import TableSpec, ValidationKey


class FakeUSDM:
    def get_klines(self, **kwargs):
        return FakeResponse([[1704067200000, "1", "2", "0.5", "1.5", "10", 1704070799999, "15", 20, "6", "9", "0"]])

    def get_funding_rate(self, **kwargs):
        return FakeResponse([{"symbol": "BTCUSDT", "fundingRate": "0.01", "fundingTime": 1704067200000, "markPrice": "42000"}])

    def get_exchange_info(self):
        return FakeResponse({"symbols": [{"symbol": "BTCUSDT", "status": "TRADING", "contractType": "PERPETUAL", "quoteAsset": "USDT", "marginAsset": "USDT", "onboardDate": 1577836800000}]})


class FakeSpot:
    def get_klines(self, **kwargs):
        return FakeResponse([[1704067200000, "1", "2", "0.5", "1.5", "10", 1704070799999, "15", 20, "6", "9", "0"]])


class FakeCoinM:
    def get_klines(self, **kwargs):
        return FakeResponse([[1704067200000, "1", "2", "0.5", "1.5", "10", 1704070799999, "15", 20, "6", "9", "0"]])

    def get_continuous_klines(self, **kwargs):
        return FakeResponse([[1704067200000, "1", "2", "0.5", "1.5", "10", 1704070799999, "15", 20, "6", "9", "0"]])

    def get_funding_rate(self, **kwargs):
        return FakeResponse([{"symbol": "BTCUSD_PERP", "fundingRate": "0.01", "fundingTime": 1704067200000}])


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def test_source_maps_usdm_kline_array_to_named_fields():
    source = BinanceSource(usdm=FakeUSDM(), spot=FakeSpot(), coinm=FakeCoinM())
    spec = TableSpec(
        table="kline_data_future_raw",
        kind="kline",
        endpoint="usdm_klines",
        key_fields=("symbol", "interval"),
        time_fields=("timestamp",),
        interval_field="interval",
        compare_fields=("timestamp", "open", "high", "low", "close", "volume"),
        request_limit=1000,
    )

    rows = source.fetch_rows(spec, ValidationKey({"symbol": "BTCUSDT", "interval": "1h"}), 1704067200000, 1704070800000)

    assert rows[0].key == 1704067200000
    assert rows[0].fields["timestamp"] == 1704067200000
    assert rows[0].fields["open_time"] == 1704067200000
    assert rows[0].fields["open"] == "1"
    assert rows[0].fields["close"] == "1.5"
    assert rows[0].fields["trades"] == 20


def test_source_maps_funding_dict_to_named_fields():
    source = BinanceSource(usdm=FakeUSDM(), spot=FakeSpot(), coinm=FakeCoinM())
    spec = TableSpec(
        table="binance_funding_rate_all_future_raw",
        kind="funding",
        endpoint="usdm_funding",
        key_fields=("symbol",),
        time_fields=("funding_time",),
        interval_field=None,
        compare_fields=("symbol", "funding_rate", "funding_time", "mark_price"),
        request_limit=1000,
    )

    rows = source.fetch_rows(spec, ValidationKey({"symbol": "BTCUSDT"}), 1704067200000, 1704070800000)

    assert rows[0].key == 1704067200000
    assert rows[0].fields["funding_rate"] == "0.01"
    assert rows[0].fields["mark_price"] == "42000"


def test_source_maps_registry_rows():
    source = BinanceSource(usdm=FakeUSDM(), spot=FakeSpot(), coinm=FakeCoinM())
    spec = TableSpec(
        table="binance_futures_symbols",
        kind="registry",
        endpoint="usdm_exchange_info",
        key_fields=("symbol",),
        time_fields=(),
        interval_field=None,
        compare_fields=("symbol", "status", "contract_type", "quote_asset", "margin_asset", "onboard_date_ms"),
        request_limit=1000,
    )

    rows = source.fetch_registry_rows(spec)

    assert rows[0].key == "BTCUSDT"
    assert rows[0].fields["contract_type"] == "PERPETUAL"
    assert rows[0].fields["onboard_date_ms"] == 1577836800000
```

- [ ] **Step 2: Run tests to verify source adapter import fails**

Run:

```bash
python3 -m pytest tests/test_db_accuracy_runner.py -q
```

Expected: FAIL with `ModuleNotFoundError` or `ImportError` for `tests.db_accuracy.binance_source`.

- [ ] **Step 3: Implement Binance source adapter**

Create `tests/db_accuracy/binance_source.py`:

```python
from __future__ import annotations

from typing import Any

from api_services.binance.coinm_market_api import COINMMarketAPI
from api_services.binance.spot_market_api import SpotMarketAPI
from api_services.binance.usdm_market_api import USDMMarketAPI
from tests.db_accuracy.models import SourceRow, TableSpec, ValidationKey


class BinanceSource:
    def __init__(self, usdm=None, spot=None, coinm=None):
        self.usdm = usdm or USDMMarketAPI()
        self.spot = spot or SpotMarketAPI()
        self.coinm = coinm or COINMMarketAPI()

    def fetch_rows(
        self,
        spec: TableSpec,
        key: ValidationKey,
        start_ms: int,
        end_ms: int,
    ) -> list[SourceRow]:
        if spec.kind == "kline":
            return self._fetch_kline_rows(spec, key, start_ms, end_ms)
        if spec.kind == "funding":
            return self._fetch_funding_rows(spec, key, start_ms, end_ms)
        raise ValueError(f"fetch_rows does not support kind={spec.kind}")

    def fetch_registry_rows(self, spec: TableSpec) -> list[SourceRow]:
        if spec.endpoint != "usdm_exchange_info":
            raise ValueError(f"Unsupported registry endpoint: {spec.endpoint}")
        payload = self.usdm.get_exchange_info().json()
        rows: list[SourceRow] = []
        for item in payload.get("symbols", []):
            fields = {
                "symbol": item.get("symbol"),
                "status": item.get("status"),
                "contract_type": item.get("contractType"),
                "quote_asset": item.get("quoteAsset"),
                "margin_asset": item.get("marginAsset"),
                "onboard_date_ms": item.get("onboardDate"),
            }
            rows.append(SourceRow(key=fields["symbol"], fields=fields))
        return rows

    def _fetch_kline_rows(
        self,
        spec: TableSpec,
        key: ValidationKey,
        start_ms: int,
        end_ms: int,
    ) -> list[SourceRow]:
        interval = spec.fixed_interval or str(key.values[spec.interval_field])
        params = {
            "interval": interval,
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": spec.request_limit,
        }
        if spec.endpoint == "usdm_klines":
            response = self.usdm.get_klines(symbol=str(key.values["symbol"]), **params)
        elif spec.endpoint == "spot_klines":
            response = self.spot.get_klines(symbol=str(key.values["symbol"]), **params)
        elif spec.endpoint == "coinm_klines":
            response = self.coinm.get_klines(symbol=str(key.values["symbol"]), **params)
        elif spec.endpoint == "coinm_continuous_klines":
            response = self.coinm.get_continuous_klines(
                pair=str(key.values["pair"]),
                contractType=str(key.values["contract_type"]),
                **params,
            )
        elif spec.endpoint == "usdm_continuous_klines":
            response = self.usdm.get_continuous_klines(
                pair=str(key.values["pair"]),
                contractType=str(key.values["contract_type"]),
                **params,
            )
        else:
            raise ValueError(f"Unsupported kline endpoint: {spec.endpoint}")

        source_rows: list[SourceRow] = []
        for raw in response.json():
            fields = {
                "timestamp": raw[0],
                "open_time": raw[0],
                "open": raw[1],
                "high": raw[2],
                "low": raw[3],
                "close": raw[4],
                "volume": raw[5],
                "close_time": raw[6],
                "quote_volume": raw[7],
                "trade_count": raw[8],
                "trades": raw[8],
                "taker_buy_base_volume": raw[9],
                "taker_buy_quote_volume": raw[10],
            }
            source_rows.append(SourceRow(key=fields["timestamp"], fields=fields))
        return source_rows

    def _fetch_funding_rows(
        self,
        spec: TableSpec,
        key: ValidationKey,
        start_ms: int,
        end_ms: int,
    ) -> list[SourceRow]:
        params = {
            "symbol": str(key.values["symbol"]),
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": spec.request_limit,
        }
        if spec.endpoint == "usdm_funding":
            response = self.usdm.get_funding_rate(**params)
        elif spec.endpoint == "coinm_funding":
            response = self.coinm.get_funding_rate(**params)
        else:
            raise ValueError(f"Unsupported funding endpoint: {spec.endpoint}")

        source_rows: list[SourceRow] = []
        for item in response.json():
            fields = {
                "symbol": item.get("symbol"),
                "funding_rate": item.get("fundingRate"),
                "funding_time": item.get("fundingTime"),
                "mark_price": item.get("markPrice"),
            }
            source_rows.append(SourceRow(key=fields["funding_time"], fields=fields))
        return source_rows
```

- [ ] **Step 4: Verify source adapter tests pass**

Run:

```bash
python3 -m pytest tests/test_db_accuracy_runner.py -q
```

Expected: source adapter tests pass.

- [ ] **Step 5: Commit source adapter**

Run:

```bash
git add tests/db_accuracy/binance_source.py tests/test_db_accuracy_runner.py
git commit -m "test: add Binance source adapter for DB accuracy"
```

Expected: commit succeeds with source adapter files staged.

---

### Task 6: Add Accuracy Runner

**Files:**
- Create: `tests/db_accuracy/runner.py`
- Modify: `tests/test_db_accuracy_runner.py`

- [ ] **Step 1: Add runner aggregation tests**

Append to `tests/test_db_accuracy_runner.py`:

```python
from tests.db_accuracy.runner import compare_db_and_source_rows, compare_registry_rows


def test_compare_db_and_source_rows_reports_missing_source_row_and_value_mismatch():
    differences = compare_db_and_source_rows(
        table="kline_data_future_raw",
        key_label="symbol=BTCUSDT,interval=1h",
        row_key_field="timestamp",
        compare_fields=("timestamp", "open"),
        db_rows=[
            {"timestamp": 1704067200000, "open": "1"},
            {"timestamp": 1704070800000, "open": "2"},
        ],
        source_rows=[
            SourceRow(key=1704067200000, fields={"timestamp": 1704067200000, "open": "3"}),
        ],
    )

    assert [diff.reason for diff in differences] == ["value_mismatch", "missing_source_row"]


def test_compare_registry_rows_reports_missing_db_row():
    differences = compare_registry_rows(
        table="binance_futures_symbols",
        compare_fields=("symbol", "status"),
        db_rows=[],
        source_rows=[SourceRow(key="BTCUSDT", fields={"symbol": "BTCUSDT", "status": "TRADING"})],
    )

    assert len(differences) == 1
    assert differences[0].reason == "missing_db_row"
```

- [ ] **Step 2: Run the new runner tests to verify import fails**

Run:

```bash
python3 -m pytest tests/test_db_accuracy_runner.py::test_compare_db_and_source_rows_reports_missing_source_row_and_value_mismatch tests/test_db_accuracy_runner.py::test_compare_registry_rows_reports_missing_db_row -q
```

Expected: FAIL with `ModuleNotFoundError` or `ImportError` for `tests.db_accuracy.runner`.

- [ ] **Step 3: Implement row-level aggregation helpers**

Create `tests/db_accuracy/runner.py`:

```python
from __future__ import annotations

import json
import time
from typing import Any

from core.db_client import DBClient
from tests.db_accuracy.binance_source import BinanceSource
from tests.db_accuracy.compare import compare_rows
from tests.db_accuracy.db_reader import DBAccuracyReader, build_windows
from tests.db_accuracy.models import (
    AccuracyRunResult,
    Difference,
    ResolvedTableSpec,
    SourceRow,
    TableRunResult,
)
from tests.db_accuracy.table_specs import load_table_specs, resolve_spec


def compare_db_and_source_rows(
    table: str,
    key_label: str,
    row_key_field: str,
    compare_fields: tuple[str, ...],
    db_rows: list[dict[str, Any]],
    source_rows: list[SourceRow],
) -> list[Difference]:
    source_by_key = {row.key: row.fields for row in source_rows}
    db_by_key = {row[row_key_field]: row for row in db_rows}
    differences: list[Difference] = []

    for row_key, db_row in db_by_key.items():
        source_row = source_by_key.get(row_key)
        if source_row is None:
            differences.append(
                Difference(
                    table=table,
                    key_label=key_label,
                    row_key=row_key,
                    field=row_key_field,
                    db_value=db_row.get(row_key_field),
                    source_value=None,
                    reason="missing_source_row",
                )
            )
            continue
        differences.extend(
            compare_rows(
                table=table,
                key_label=key_label,
                row_key=row_key,
                db_row=db_row,
                source_row=source_row,
                fields=compare_fields,
            )
        )

    for row_key, source_row in source_by_key.items():
        if row_key not in db_by_key:
            differences.append(
                Difference(
                    table=table,
                    key_label=key_label,
                    row_key=row_key,
                    field=row_key_field,
                    db_value=None,
                    source_value=source_row.get(row_key_field),
                    reason="missing_db_row",
                )
            )

    return differences


def compare_registry_rows(
    table: str,
    compare_fields: tuple[str, ...],
    db_rows: list[dict[str, Any]],
    source_rows: list[SourceRow],
) -> list[Difference]:
    source_by_symbol = {row.key: row.fields for row in source_rows}
    db_by_symbol = {row["symbol"]: row for row in db_rows}
    differences: list[Difference] = []

    for symbol, db_row in db_by_symbol.items():
        source_row = source_by_symbol.get(symbol)
        if source_row is None:
            differences.append(
                Difference(
                    table=table,
                    key_label=f"symbol={symbol}",
                    row_key=symbol,
                    field="symbol",
                    db_value=symbol,
                    source_value=None,
                    reason="missing_source_row",
                )
            )
            continue
        differences.extend(
            compare_rows(
                table=table,
                key_label=f"symbol={symbol}",
                row_key=symbol,
                db_row=db_row,
                source_row=source_row,
                fields=compare_fields,
            )
        )

    for symbol, source_row in source_by_symbol.items():
        if symbol not in db_by_symbol:
            differences.append(
                Difference(
                    table=table,
                    key_label=f"symbol={symbol}",
                    row_key=symbol,
                    field="symbol",
                    db_value=None,
                    source_value=source_row.get("symbol"),
                    reason="missing_db_row",
                )
            )

    return differences


class AccuracyRunner:
    def __init__(self, db=None, source=None):
        self.db = db or DBClient()
        self.reader = DBAccuracyReader(self.db)
        self.source = source or BinanceSource()

    def run(self, safety_hours: int, include_tables: list[str] | None = None) -> AccuracyRunResult:
        stable_before_ms = int(time.time() * 1000) - safety_hours * 3_600_000
        selected_tables = set(include_tables or [])
        result = AccuracyRunResult()

        for spec in load_table_specs():
            if selected_tables and spec.table not in selected_tables:
                continue

            table_result = TableRunResult(table=spec.table)
            try:
                columns = self.reader.table_columns(spec.table)
                resolved = resolve_spec(spec, columns)
                if spec.kind == "registry":
                    self._run_registry(resolved, table_result)
                else:
                    self._run_time_series(resolved, stable_before_ms, table_result)
            except Exception as exc:
                table_result.differences.append(
                    Difference(
                        table=spec.table,
                        key_label="table",
                        row_key="table",
                        field="table",
                        db_value=None,
                        source_value=None,
                        reason=f"table_error:{type(exc).__name__}:{exc}",
                    )
                )
            result.tables.append(table_result)

        return result

    def _run_time_series(
        self,
        spec: ResolvedTableSpec,
        stable_before_ms: int,
        table_result: TableRunResult,
    ) -> None:
        if spec.time_field is None:
            raise ValueError(f"{spec.spec.table} has no resolved time field")

        for key_range in self.reader.key_ranges(spec, stable_before_ms):
            for start_ms, end_ms in build_windows(spec, key_range):
                table_result.windows_checked += 1
                db_rows = self.reader.rows_for_window(spec, key_range.key, start_ms, end_ms)
                source_rows = self.source.fetch_rows(spec.spec, key_range.key, start_ms, end_ms)
                table_result.db_rows_checked += len(db_rows)
                table_result.source_rows_checked += len(source_rows)
                table_result.differences.extend(
                    compare_db_and_source_rows(
                        table=spec.spec.table,
                        key_label=key_range.key.label(),
                        row_key_field=spec.time_field,
                        compare_fields=spec.compare_fields,
                        db_rows=db_rows,
                        source_rows=source_rows,
                    )
                )

    def _run_registry(self, spec: ResolvedTableSpec, table_result: TableRunResult) -> None:
        db_rows = self.reader.registry_rows(spec)
        source_rows = self.source.fetch_registry_rows(spec.spec)
        table_result.windows_checked = 1
        table_result.db_rows_checked = len(db_rows)
        table_result.source_rows_checked = len(source_rows)
        table_result.differences.extend(
            compare_registry_rows(
                table=spec.spec.table,
                compare_fields=spec.compare_fields,
                db_rows=db_rows,
                source_rows=source_rows,
            )
        )


def result_to_json(result: AccuracyRunResult) -> str:
    payload = {
        "passed": result.passed,
        "tables": [
            {
                "table": table.table,
                "passed": table.passed,
                "windows_checked": table.windows_checked,
                "db_rows_checked": table.db_rows_checked,
                "source_rows_checked": table.source_rows_checked,
                "differences": [difference.__dict__ for difference in table.differences],
            }
            for table in result.tables
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)
```

- [ ] **Step 4: Fix imports in `tests/test_db_accuracy_runner.py`**

Ensure this import appears near the top:

```python
from tests.db_accuracy.models import SourceRow
```

- [ ] **Step 5: Verify runner tests pass**

Run:

```bash
python3 -m pytest tests/test_db_accuracy_runner.py -q
```

Expected: all source adapter and runner tests pass.

- [ ] **Step 6: Commit runner**

Run:

```bash
git add tests/db_accuracy/runner.py tests/test_db_accuracy_runner.py
git commit -m "test: add DB accuracy runner"
```

Expected: commit succeeds with runner files staged.

---

### Task 7: Replace Smoke Test With Full Manual Pytest Entry

**Files:**
- Modify: `tests/test_binance_db_accuracy.py`
- Test: `tests/test_binance_db_accuracy.py`

- [ ] **Step 1: Replace the smoke test with the manual full-run entry**

Replace `tests/test_binance_db_accuracy.py` with:

```python
import allure
import pytest

from tests.db_accuracy.runner import AccuracyRunner, result_to_json


pytestmark = pytest.mark.db_accuracy


@allure.title("DB-ACC-BINANCE-FULL-001 - Binance raw/metadata DB rows match upstream REST source")
@pytest.mark.dqc
def test_binance_raw_and_metadata_db_accuracy(request):
    """
    Case ID: DB-ACC-BINANCE-FULL-001
    测试目的: 全量校验 PDF 范围内 Binance raw/metadata 表中的稳定历史数据与 Binance REST 上游严格一致。
    """
    safety_hours = request.config.getoption("--db-accuracy-safety-hours")
    include_tables = request.config.getoption("--db-accuracy-table")

    result = AccuracyRunner().run(
        safety_hours=safety_hours,
        include_tables=include_tables,
    )

    allure.attach(
        result.summary_text(),
        name="db_accuracy_summary",
        attachment_type=allure.attachment_type.TEXT,
    )
    allure.attach(
        result_to_json(result),
        name="db_accuracy_details",
        attachment_type=allure.attachment_type.JSON,
    )

    assert result.passed, result.summary_text()
```

- [ ] **Step 2: Run without flag and verify it skips**

Run:

```bash
python3 -m pytest tests/test_binance_db_accuracy.py -q
```

Expected: `1 skipped`.

- [ ] **Step 3: Run collect-only with flag and verify the test is available**

Run:

```bash
python3 -m pytest tests/test_binance_db_accuracy.py --collect-only -q --run-db-accuracy
```

Expected:

```text
tests/test_binance_db_accuracy.py::test_binance_raw_and_metadata_db_accuracy
1 test collected
```

- [ ] **Step 4: Run a targeted live validation only when DB and Binance access are configured**

Run this command in a real environment with `config/.env.test` or the selected `--env` configured:

```bash
python3 -m pytest tests/test_binance_db_accuracy.py -q --run-db-accuracy --db-accuracy-table binance_futures_symbols
```

Expected in a configured environment: the test reaches Binance and MySQL, attaches `db_accuracy_summary` and `db_accuracy_details`, and either passes or fails with concrete row differences. If DB credentials are not configured, expected failure is a table error for `binance_futures_symbols`.

- [ ] **Step 5: Commit pytest entry**

Run:

```bash
git add tests/test_binance_db_accuracy.py
git commit -m "test: add manual Binance DB accuracy pytest entry"
```

Expected: commit succeeds with only `tests/test_binance_db_accuracy.py` staged.

---

### Task 8: Add Operator Documentation

**Files:**
- Create: `docs/binance_db_accuracy_validation.md`
- Modify: `README.md`

- [ ] **Step 1: Create DB accuracy operator guide**

Create `docs/binance_db_accuracy_validation.md`:

````markdown
# Binance DB Accuracy Validation

## Purpose

This manual pytest suite compares Binance raw/metadata rows in MySQL against Binance REST source data. It is designed for full historical validation, not default CI.

## Scope

Included tables are configured in `data/binance_db_accuracy_tables.yaml`.

The first version validates Binance raw/metadata only:

- K line raw tables
- funding raw tables
- COIN-M and USDM delivery raw tables
- `binance_futures_symbols`

The first version does not validate clean/curated tables, CoinGlass tables, DQC issue tables, repair tables, or derived application summaries.

## Run

Run all configured Binance DB accuracy checks:

```bash
python3 -m pytest tests/test_binance_db_accuracy.py -v --run-db-accuracy
```

Run one table:

```bash
python3 -m pytest tests/test_binance_db_accuracy.py -v --run-db-accuracy --db-accuracy-table binance_futures_symbols
```

Use a larger safety window when recent rows are still being written or Binance data may still be moving:

```bash
python3 -m pytest tests/test_binance_db_accuracy.py -v --run-db-accuracy --db-accuracy-safety-hours 48
```

## Report

The test attaches two Allure artifacts:

- `db_accuracy_summary`: compact per-table counts
- `db_accuracy_details`: JSON details for every mismatch

The run continues through all configured tables and fails at the end if any mismatch is found.

## Strictness

Numeric values are compared with Decimal normalization. For example, `1.2300` and `1.23` are equal. No tolerance is applied.

## Default CI Behavior

The suite is skipped unless `--run-db-accuracy` is passed. Default `pytest` and existing CI flows do not run the full DB accuracy validation.
````

- [ ] **Step 2: Add README pointer**

Append this section near the existing "数据库一致性校验" section in `README.md`:

````markdown
### 3. Binance 数据库准确性全量校验

`tests/test_binance_db_accuracy.py` 提供手动触发的 Binance raw/metadata 表全量准确性校验。它会从 MySQL 扫描 PDF 范围内的 Binance raw/metadata 表，并与 Binance REST 上游严格对账。

默认 `pytest` 不运行该套件；需要显式传入：

```bash
python3 -m pytest tests/test_binance_db_accuracy.py -v --run-db-accuracy
```

更多运行方式见 `docs/binance_db_accuracy_validation.md`。
````

- [ ] **Step 3: Verify docs mention the manual flag**

Run:

```bash
rg -n -- "--run-db-accuracy|binance_db_accuracy_validation" README.md docs/binance_db_accuracy_validation.md
```

Expected: both files contain the manual flag and docs path.

- [ ] **Step 4: Commit docs**

Run:

```bash
git add README.md docs/binance_db_accuracy_validation.md
git commit -m "docs: document Binance DB accuracy validation"
```

Expected: commit succeeds with docs files staged.

---

### Task 9: Final Verification

**Files:**
- Verify all files changed in prior tasks.

- [ ] **Step 1: Run focused unit tests**

Run:

```bash
python3 -m pytest tests/test_db_accuracy_config.py tests/test_db_accuracy_compare.py tests/test_db_accuracy_runner.py -q
```

Expected: all unit tests pass.

- [ ] **Step 2: Verify manual suite is skipped by default**

Run:

```bash
python3 -m pytest tests/test_binance_db_accuracy.py -q
```

Expected: `1 skipped`.

- [ ] **Step 3: Verify manual suite collection with explicit flag**

Run:

```bash
python3 -m pytest tests/test_binance_db_accuracy.py --collect-only -q --run-db-accuracy
```

Expected: `1 test collected`.

- [ ] **Step 4: Run static Python compilation**

Run:

```bash
python3 -m py_compile tests/db_accuracy/models.py tests/db_accuracy/table_specs.py tests/db_accuracy/db_reader.py tests/db_accuracy/binance_source.py tests/db_accuracy/compare.py tests/db_accuracy/runner.py tests/test_binance_db_accuracy.py
```

Expected: no output and exit code 0.

- [ ] **Step 5: Run full manual validation in the configured environment**

Run:

```bash
python3 -m pytest tests/test_binance_db_accuracy.py -v --run-db-accuracy
```

Expected in a fully configured environment: the suite scans all configured Binance tables, attaches Allure summary/detail artifacts, continues across all differences, and exits PASS only when all stable historical rows match Binance source.

- [ ] **Step 6: Commit final verification fixes if any were needed**

If a verification step required a code fix, run:

```bash
git add data/binance_db_accuracy_tables.yaml tests/db_accuracy tests/test_db_accuracy_config.py tests/test_db_accuracy_compare.py tests/test_db_accuracy_runner.py tests/test_binance_db_accuracy.py tests/conftest.py pytest.ini README.md docs/binance_db_accuracy_validation.md
git commit -m "test: finalize Binance DB accuracy validation"
```

Expected: commit succeeds only when new fixes were made after the prior commits.

---

## Self-Review

- Spec coverage: The plan covers Binance-only raw/metadata scope, exhaustive table/key/window scanning, strict no-tolerance comparison, safety-window exclusion, pytest/Allure entry, report-only output, no checkpoint persistence, continue-through-failure aggregation, and default CI skip behavior.
- Scope check: This is one coherent subsystem: Binance database-to-source accuracy validation. CoinGlass and clean/curated validation remain outside this plan.
- Type consistency: The plan defines `TableSpec`, `ResolvedTableSpec`, `ValidationKey`, `SourceRow`, `Difference`, `TableRunResult`, and `AccuracyRunResult` before using them in later tasks.
- Ambiguity handled: Missing configured DB tables are table-level failures, not silent skips. Missing source rows and missing DB rows are explicit difference reasons.
