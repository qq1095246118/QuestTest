# Agent Guide

This is the quick-start map for future agents working in this repository.
Read this file first, then open the referenced docs only when deeper context is
needed.

## Project Purpose

QuestTest is a Python 3.12 pytest automation framework for a quantitative data
platform. The goal is not just API availability. Tests protect financial data
quality: response contracts, millisecond timestamps, OHLC logic, and strict
consistency between platform database rows and upstream Binance REST data.

## Current Architecture

| Path | Role |
|---|---|
| `config/` | Environment configuration. `settings.py` loads `config/.env.<env>` according to `TEST_ENV`. |
| `infrastructure/` | Protected framework foundations: HTTP retry, DB client/DAO, and reusable assertions. |
| `api/` | Raw API request wrappers only. Internal platform APIs live under `api/platform/`; Binance upstream wrappers live under `api/external/binance/`. |
| `services/` | Reusable logic, judgment, comparison, caching, and report data preparation. |
| `data/` | YAML test data and DB accuracy table specs. |
| `tests/` | Executable pytest tests only, organized by business domain first. |
| `tools/` | Directly runnable utility scripts and temporary Python files. |
| `artifacts/reports/` | Manual CSV/XLSX/JSON report outputs. |
| `docs/` | Architecture rules, test design notes, and DB accuracy usage docs. |

## Test Layout

Tests are business-domain first, then grouped by the tested layer or entrypoint
type:

```text
tests/
  kline/api/
  binance/api/
  coinglass/api/
  factor_data/api/
  open_interest/api/
  db_accuracy/
    integration/
    services/
    tools/
```

Important distinction: root-level `tools/` contains runnable tool
implementations. `tests/db_accuracy/tools/` contains pytest tests for those
tools; it must not contain tool implementation code.

## Hard Rules

- Do not modify `infrastructure/` unless the user explicitly asks. The repo's
  own `docs/AI_GENERATION_GUIDE.md` treats it as protected infrastructure.
- Keep raw HTTP/API wrappers in `api/`. Do not put business comparison or
  judgment logic there.
- Put reusable business logic in `services/`, not in `tests/` or runnable tool
  scripts.
- Put executable pytest files under `tests/<business_domain>/<test_type>/`.
- Put runnable utilities and temporary Python scripts under `tools/`.
- Do not invent tests outside the documented data-platform scope. Stay within
  the tables and APIs described in `docs/`.
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

Run a business-domain slice:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/kline/api -v
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/db_accuracy/services -q
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/db_accuracy/tools -q
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
6. Serialize Allure JSON/text through
   `services/db_accuracy/reporting/result_serializer_service.py`.

Cached mode:

1. Validate one-table request and required time range.
2. Build explicit or DB-discovered market shards.
3. Split time into partitions.
4. Cache Binance source data as Parquet plus manifest through services under
   `services/db_accuracy/cached/`.
5. Read DB rows for the same shard and partition.
6. Normalize frames and compare with DataComPy.
7. Write report and diff artifacts under the cache report directory.

Start with `docs/binance_db_accuracy_validation.md` before changing this area.

## Generated Outputs

- `allure-results/` is pytest/Allure output and is cleaned by pytest defaults.
- `artifacts/reports/` is the current manual report output directory.
- `.cache/binance_accuracy/` is the default cached DB accuracy source-data cache.
- A legacy generated `reports/` directory may exist in local worktrees. Do not
  clean it unless the user asks.

## Where To Look First

- Project rules: `docs/AI_GENERATION_GUIDE.md`
- Project overview: `README.md`
- DB accuracy docs: `docs/binance_db_accuracy_validation.md`
- DB accuracy specs: `data/binance_db_accuracy_tables.yaml`
- Pytest hooks and CLI flags: `tests/conftest.py`
- DB accuracy entrypoint: `tests/db_accuracy/integration/test_binance_db_accuracy.py`
- Direct DB accuracy service: `services/db_accuracy/direct/accuracy_service.py`
- Cached DB accuracy service: `services/db_accuracy/cached/cached_accuracy_service.py`
- Binance source mapping: `services/db_accuracy/source_service.py`
- Report workbook generator: `tools/db_accuracy/build_allure_xlsx.py`

## Known Caveats

- The worktree may contain unrelated deleted plan docs, `.superpowers/`, and
  generated report files. Do not clean them up unless the user asks.
- Traditional API test files are large and explicit. Match their style for small
  additions; add helpers only when duplication creates real maintenance cost.
- Full live API runs depend on `BASE_URL`, Binance network access, and DB
  credentials. Prefer collection and focused service/tool tests when verifying
  pure structure changes.
