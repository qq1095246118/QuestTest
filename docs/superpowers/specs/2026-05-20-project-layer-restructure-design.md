# Project Layer Restructure Design

## Background

QuestTest is a Python 3.12 pytest automation framework for a quantitative data
platform. The current codebase works, but several directories now carry mixed
responsibilities:

- `tests/` contains executable pytest cases and reusable DB accuracy engine code.
- `api_services/` contains both internal platform API wrappers and external
  Binance REST wrappers.
- `core/` contains low-level HTTP, database, and assertion infrastructure, but
  its name does not describe the layer clearly enough for future maintenance.
- `scripts/` contains directly runnable tools and reusable intermediate logic.
- Generated reports are currently written under `reports/`, separate from a
  clearer artifact boundary.

This redesign makes the project layers explicit and moves the codebase to the
new structure in one implementation pass.

## Goals

- Make each top-level directory have a single, clear purpose.
- Keep `tests/` limited to executable pytest test files.
- Create an `api/` layer for raw API call wrappers only.
- Create a `services/` layer for intermediate logic, data comparison, judgment,
  caching, normalization, and report data preparation.
- Move low-level technical capabilities from `core/` to `infrastructure/`.
- Move directly runnable tools and temporary Python utilities into `tools/`.
- Move manually generated CSV/XLSX/JSON reports into `artifacts/reports/`.
- Update imports, class names, tests, and documentation to match the new layout.
- Add useful Chinese module, class, function, and key-logic comments without
  adding noisy line-by-line comments.

## Non-Goals

- Do not add new business API test coverage.
- Do not run or require real DB accuracy historical comparisons as part of this
  migration.
- Do not preserve old import-path compatibility wrappers.
- Do not keep `scripts/`, `api_services/`, `core/`, or `tests/db_accuracy/` as
  active legacy structure directories.
- Do not move `config/` or `data/` into another layer.
- Do not mechanically comment every physical line of code.

## Target Directory Structure

```text
api/
  base_api.py
  platform/
    kline_data_api.py
    binance_full_api.py
    binance_usdm_api.py
    coinglass_api.py
    factor_data_api.py
    open_interest_api.py
  external/
    binance/
      spot_market_api.py
      usdm_market_api.py
      coinm_market_api.py

services/
  db_accuracy/
    models.py
    table_specs.py
    compare_service.py
    source_service.py
    db_reader_service.py
    direct/
      accuracy_service.py
    cached/
      cache_models.py
      cache_store_service.py
      cached_source_service.py
      cached_db_reader_service.py
      cached_accuracy_service.py
      shard_planner_service.py
      frame_normalizer_service.py
      datacompy_service.py
    reporting/
      result_serializer_service.py
  reports/
  exports/

infrastructure/
  http/
    http_client.py
  database/
    db_client.py
    dao.py
  assertions/
    dqc_asserts.py
    logic_asserts.py

tools/
  db_accuracy/
    build_allure_xlsx.py
    fetch_selected_usdm_klines.py
    build_selected_usdm_klines_xlsx.py

tests/
  conftest.py
  api/
    test_kline_api.py
    test_binance_full_api.py
    test_binance_usdm_api.py
    test_factor_data_api.py
    test_open_interest_api.py
    test_coinglass_api.py
  services/
    db_accuracy/
      test_compare_service.py
      test_table_specs.py
      test_db_reader_service.py
      test_direct_accuracy_service.py
      test_cached_accuracy_service.py
      test_cached_source_service.py
      test_cache_store_service.py
      test_frame_normalizer_service.py
      test_datacompy_service.py
      test_shard_planner_service.py
  integration/
    test_binance_db_accuracy.py
  tools/
    test_build_db_accuracy_allure_xlsx.py

config/
data/
docs/
artifacts/
  reports/
```

## Layer Responsibilities

### `api/`

`api/` stores raw API call wrappers. These modules build endpoints, pass
`params` or JSON bodies, and delegate HTTP behavior to `infrastructure/http`.
They must not perform business decisions, DB comparisons, report generation, or
complex assertions.

Internal platform endpoints move under `api/platform/`. External upstream REST
wrappers move under `api/external/`.

### `services/`

`services/` stores intermediate logic and judgment. Services can combine API
calls, database reads, file/cache state, normalization, comparison, and report
payload preparation.

DB accuracy logic moves here from `tests/db_accuracy/`. This makes the pytest
directory a true test-only tree while keeping the comparison engine reusable by
tests and tools.

### `infrastructure/`

`infrastructure/` replaces `core/` as the low-level technical foundation. It
contains HTTP retry behavior, database clients and DAO helpers, and reusable DQC
or financial logic assertions.

This layer must not import from `api/`, `services/`, `tests/`, or `tools/`.

### `tools/`

`tools/` stores directly runnable utility scripts and temporary Python files.
Tools should organize CLI-style parameters, call services, and write outputs.
Reusable logic must live in `services/`, not in tool scripts.

Tools are run by file path, for example:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 tools/db_accuracy/build_allure_xlsx.py
```

### `tests/`

`tests/` stores only executable pytest test files and pytest support files such
as `conftest.py`. The first directory level is the business or capability
domain, such as `kline/`, `binance/`, `coinglass/`, `factor_data/`,
`open_interest/`, or `db_accuracy/`. Inside each domain, tests may be grouped by
tested layer or entrypoint type, such as `api/`, `services/`, `integration/`, or
`tools/`.

### `artifacts/`

`artifacts/reports/` stores manually generated CSV, XLSX, and JSON reports.
`.cache/binance_accuracy/` remains the cache location for cached source data,
and `allure-results/` remains the pytest/Allure default output path.

## Migration Map

### API Layer

```text
api_services/base_api.py                  -> api/base_api.py
api_services/kline_data_api.py            -> api/platform/kline_data_api.py
api_services/binance_full_api.py          -> api/platform/binance_full_api.py
api_services/binance_usdm_api.py          -> api/platform/binance_usdm_api.py
api_services/coinglass_api.py             -> api/platform/coinglass_api.py
api_services/factor_data_api.py           -> api/platform/factor_data_api.py
api_services/open_interest_api.py         -> api/platform/open_interest_api.py
api_services/binance/spot_market_api.py   -> api/external/binance/spot_market_api.py
api_services/binance/usdm_market_api.py   -> api/external/binance/usdm_market_api.py
api_services/binance/coinm_market_api.py  -> api/external/binance/coinm_market_api.py
```

API wrapper class names keep the existing `API` suffix, such as
`KlineDataAPI`, `BinanceFullAPI`, and `USDMMarketAPI`.

### Infrastructure Layer

```text
core/http_client.py     -> infrastructure/http/http_client.py
core/db_client.py       -> infrastructure/database/db_client.py
core/dao.py             -> infrastructure/database/dao.py
core/dqc_asserts.py     -> infrastructure/assertions/dqc_asserts.py
core/logic_asserts.py   -> infrastructure/assertions/logic_asserts.py
```

### DB Accuracy Service Layer

```text
tests/db_accuracy/models.py             -> services/db_accuracy/models.py
tests/db_accuracy/table_specs.py        -> services/db_accuracy/table_specs.py
tests/db_accuracy/compare.py            -> services/db_accuracy/compare_service.py
tests/db_accuracy/binance_source.py     -> services/db_accuracy/source_service.py
tests/db_accuracy/db_reader.py          -> services/db_accuracy/db_reader_service.py
tests/db_accuracy/runner.py             -> services/db_accuracy/direct/accuracy_service.py
tests/db_accuracy/cache_models.py       -> services/db_accuracy/cached/cache_models.py
tests/db_accuracy/cache_store.py        -> services/db_accuracy/cached/cache_store_service.py
tests/db_accuracy/cached_source.py      -> services/db_accuracy/cached/cached_source_service.py
tests/db_accuracy/cached_db_reader.py   -> services/db_accuracy/cached/cached_db_reader_service.py
tests/db_accuracy/cached_runner.py      -> services/db_accuracy/cached/cached_accuracy_service.py
tests/db_accuracy/shard_planner.py      -> services/db_accuracy/cached/shard_planner_service.py
tests/db_accuracy/frame_normalizer.py   -> services/db_accuracy/cached/frame_normalizer_service.py
tests/db_accuracy/datacompy_engine.py   -> services/db_accuracy/cached/datacompy_service.py
```

The integration pytest entrypoint lives at:

```text
tests/db_accuracy/integration/test_binance_db_accuracy.py
```

### Tools

```text
scripts/build_db_accuracy_allure_xlsx.py        -> tools/db_accuracy/build_allure_xlsx.py
scripts/fetch_selected_usdm_1h_klines.py        -> tools/db_accuracy/fetch_selected_usdm_klines.py
scripts/build_selected_usdm_1h_klines_xlsx.py   -> tools/db_accuracy/build_selected_usdm_klines_xlsx.py
```

Report output paths in these tools should move from `reports/` to
`artifacts/reports/`.

### Tests

Current root-level tests move under business-domain directories. For example,
Kline API tests move under `tests/kline/api/`, Binance API tests move under
`tests/binance/api/`, DB accuracy service unit tests move under
`tests/db_accuracy/services/`, tool tests move under `tests/db_accuracy/tools/`,
and the DB accuracy integration entrypoint moves under
`tests/db_accuracy/integration/`.

## DB Accuracy Design

DB accuracy becomes a reusable service package. The pytest integration entrypoint
is only responsible for reading pytest options, invoking services, and attaching
Allure payloads.

Direct mode flow:

1. Load table specs from `data/binance_db_accuracy_tables.yaml`.
2. Resolve actual DB columns.
3. Discover stable DB time ranges.
4. Build request windows.
5. Fetch matching source rows through `api.external.binance`.
6. Compare DB and source rows.
7. Serialize summary text and JSON details for Allure.

Cached mode flow:

1. Validate one-table request and required time range.
2. Build explicit or DB-discovered market shards.
3. Split the requested time range into partitions.
4. Cache Binance source data as Parquet plus manifest under `.cache/binance_accuracy`.
5. Read DB rows for the same shard and partition.
6. Normalize DB and source frames.
7. Compare frames with DataComPy.
8. Write report and diff artifacts under the cache report directory.
9. Return structured service results to the integration test.

## Naming Rules

- API layer classes keep `API` suffixes.
- Service layer classes use `Service` suffixes where they represent behavior.
- Pure data structures do not receive artificial `Service` suffixes.

Planned service renames:

```text
AccuracyRunner        -> DirectAccuracyService
CachedAccuracyRunner  -> CachedAccuracyService
BinanceSource         -> BinanceSourceService
DBAccuracyReader      -> DBAccuracyReaderService
CachedDBReader        -> CachedDBReaderService
CachedBinanceSource   -> CachedBinanceSourceService
DataComPyEngine       -> DataComPyCompareService
CacheStore            -> CacheStoreService
```

Data classes such as `TableSpec`, `CachedCompareRequest`, `MarketShard`, and
`TimePartition` keep data-oriented names.

## Chinese Commenting Standard

The migration should add maintainable Chinese explanations:

- Each migrated module gets a Chinese module docstring explaining responsibility
  and non-responsibility.
- Each class gets a Chinese docstring.
- Each function or method gets a Chinese docstring explaining input, output, and
  key side effects.
- Complex blocks such as time-window splitting, numeric normalization, cache
  status handling, and DataComPy comparison get concise Chinese comments.
- Simple imports, assignments, and obvious returns do not receive noisy comments.
- Tests keep readable case docstrings, Allure titles, and focused comments only
  where they clarify intent.

## Documentation Updates

Update all docs that mention old paths, commands, or directory rules:

- `README.md`
- `AGENTS.md`
- `docs/AI_GENERATION_GUIDE.md`
- `docs/binance_db_accuracy_validation.md`
- `docs/kline_traditional_test_report.md`
- Related test case documents that include old command paths or old layer rules.

The old rule "new API encapsulations go into `api_services/`" becomes "raw API
wrappers go into `api/`". The old protected `core/` rule becomes an
`infrastructure/` rule.

## Validation

DB accuracy historical comparison is not part of this migration validation.
The validation target is ordinary pytest without `--run-db-accuracy`.

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest --collect-only -q
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest -v
```

Expected behavior:

- Collection succeeds after all import paths are updated.
- Ordinary pytest passes according to the configured environment.
- DB accuracy integration remains skipped unless `--run-db-accuracy` is provided.
- Service and tool unit tests that do not require real DB comparison pass.

## Implementation Order

1. Create new package directories and `__init__.py` files.
2. Move `core/` modules into `infrastructure/` and update imports.
3. Move `api_services/` modules into `api/` and update imports.
4. Move DB accuracy engine modules into `services/db_accuracy/`.
5. Rename service classes and update service imports.
6. Move scripts into `tools/db_accuracy/`, moving reusable logic into services.
7. Move pytest files into business-domain directories under `tests/`, then group
   them by `api/`, `services/`, `integration/`, or `tools/` inside each domain.
8. Update report output paths to `artifacts/reports/`.
9. Update documentation and `AGENTS.md`.
10. Run collection and ordinary pytest validation.

## Open Risk Areas

- The migration is intentionally large and will touch many import paths.
- Renaming service classes while moving files increases the chance of missed
  references.
- Ordinary pytest may still depend on a configured live API environment. If the
  environment is unavailable, failures must be separated from migration failures.
- Tool output path changes require matching documentation and tests so generated
  files are discoverable.
