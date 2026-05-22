# Agent Guide

This file is the quick-start map for future agents working in this repository.
Read it first, then open the referenced files only when deeper context is needed.

## Project Purpose

QuestTest is a Python 3.12 pytest automation framework for a quantitative data
platform. The important goal is not only API availability. Tests protect financial
data quality: response contracts, millisecond timestamps, OHLC logic, and strict
consistency between platform database rows and upstream Binance REST data.

## Current Shape

- `README.md` is the project overview and main architecture guide.
- `config/settings.py` loads `config/.env.<env>` according to `TEST_ENV`.
- `api/` contains raw internal platform and external upstream API wrappers.
- `services/` contains intermediate logic and DB accuracy services.
- `infrastructure/` contains HTTP, database, DAO, and assertion foundations.
- `tools/` contains directly runnable utility scripts and temporary Python files.
- `tests/` contains executable pytest tests only. It is organized by business
  domain first, then by tested layer or entrypoint type.
- `data/binance_db_accuracy_tables.yaml` defines the Binance DB accuracy table
  specs: table kind, endpoint, key fields, time fields, and compare fields.
- `docs/` contains test case designs, project constraints, and DB accuracy usage docs.
- `artifacts/reports/` contains manually generated report outputs.

## Hard Rules

- Do not modify `infrastructure/` unless the user explicitly asks. The repo's own
  `docs/AI_GENERATION_GUIDE.md` treats it as protected infrastructure.
- Do not invent tests outside the documented data-platform scope. Stay within the
  tables and APIs described in `docs/`, especially the PDF-backed Binance and
  platform data surfaces.
- For financial data checks, prefer helpers in `infrastructure/assertions/` or
  service-level validators over generic assertions.
- Preserve user work. The worktree may already be dirty.
- Use `apply_patch` for manual edits.

## Environment

Recommended Python:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12
```

Install dependencies:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pip install -r requirements.txt
```

Create local config from the template when needed:

```bash
cp config/.env.example config/.env.test
```

Real API and DB tests need valid values in `config/.env.<env>`.

## Useful Commands

Safe collection check:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest --collect-only -q
```

Run the normal suite:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest -v
```

Run against another environment:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest -v --env=prod
```

Run the manual Binance DB accuracy suite:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/db_accuracy/integration/test_binance_db_accuracy.py -v --run-db-accuracy
```

Run cached DB accuracy for one table and time range:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/db_accuracy/integration/test_binance_db_accuracy.py -v \
  --run-db-accuracy \
  --db-accuracy-mode cached \
  --db-accuracy-table binance_kline_all_future_raw \
  --db-accuracy-symbol BTCUSDT \
  --db-accuracy-interval 1m \
  --db-accuracy-start-ms 1704067200000 \
  --db-accuracy-end-ms 1704153599999
```

## DB Accuracy Mental Model

Direct mode:

1. Load table specs from `data/binance_db_accuracy_tables.yaml`.
2. Resolve actual DB columns with `services/db_accuracy/table_specs.py`.
3. Discover stable DB time ranges with `services/db_accuracy/db_reader_service.py`.
4. Fetch matching upstream rows through `services/db_accuracy/source_service.py`.
5. Compare rows through `services/db_accuracy/direct/accuracy_service.py`.
6. Serialize Allure JSON/text through `services/db_accuracy/reporting/result_serializer_service.py`.

Cached mode:

1. Validate one-table request and required time range.
2. Build explicit or DB-discovered market shards.
3. Split time into partitions.
4. Cache Binance source data as Parquet plus manifest through services under
   `services/db_accuracy/cached/`.
5. Read DB rows for the same shard and partition.
6. Normalize frames and compare with DataComPy.
7. Write report and diff artifacts through cached report services under
   `services/db_accuracy/cached/`.

Start with `docs/binance_db_accuracy_validation.md` before changing this area.

## Test Suite Notes

- Normal pytest collection currently discovers hundreds of tests across live API
  suites and DB accuracy unit tests.
- Test directories are business-domain first: for example `tests/kline/api/`,
  `tests/binance/api/`, and `tests/db_accuracy/services/`.
- `tests/db_accuracy/tools/` contains tests for the runnable scripts in
  root-level `tools/db_accuracy/`; tool implementation code stays under `tools/`.
- `tests/db_accuracy/integration/test_binance_db_accuracy.py` is skipped unless `--run-db-accuracy` is set.
- Live API tests skip or fail depending on whether `BASE_URL`, Binance network
  access, and DB settings are available.
- `pytest.ini` writes Allure results to `./allure-results` and cleans that
  directory by default.
- `tests/conftest.py` registers custom CLI flags and injects Allure metadata.

## Known Caveats

- The worktree may contain unrelated deleted plan docs and generated `artifacts/reports/`
  files. Do not clean them up unless the user asks.
- Traditional API test files are large and explicit. Prefer matching their style
  for small additions, but consider focused helpers only when duplication becomes
  a real maintenance problem.

## Where To Look First

- Project rules: `docs/AI_GENERATION_GUIDE.md`
- DB accuracy docs: `docs/binance_db_accuracy_validation.md`
- DB accuracy specs: `data/binance_db_accuracy_tables.yaml`
- Pytest hooks and CLI flags: `tests/conftest.py`
- DB accuracy entrypoint: `tests/db_accuracy/integration/test_binance_db_accuracy.py`
- Cached runner: `services/db_accuracy/cached/cached_accuracy_service.py`
- Direct runner: `services/db_accuracy/direct/accuracy_service.py`
- Binance source mapping: `services/db_accuracy/source_service.py`
- Report workbook generator: `tools/db_accuracy/build_allure_xlsx.py`
