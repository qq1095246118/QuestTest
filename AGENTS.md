# Agent Guide

QuestTest is a single-purpose Python 3.12 pytest project.

Its only responsibilities are:

- platform API automation tests
- reusable HTTP/assertion support for those tests
- Allure result metadata and report output

Anything unrelated to API automation and Allure output belongs outside
QuestTest.

## Project Layout

| Path | Role |
|---|---|
| `api/base_api.py` | Base request wrapper used by platform API clients. |
| `api/platform/` | Raw platform API request wrappers. |
| `config/` | Environment settings loaded from `config/env.<env>`. |
| `data/` | API test parameter data. |
| `infrastructure/http/` | HTTP client and retry behavior. |
| `infrastructure/assertions/` | DQC and financial logic assertions. |
| `tests/<domain>/api/` | Executable pytest API tests. |
| `docs/` | API automation notes only. |
| `pytest.ini` | pytest and Allure defaults. |

## Test Domains

```text
tests/
  binance/api/
  coinglass/api/
  factor_data/api/
  kline/api/
  open_interest/api/
```

## Hard Rules

- Keep QuestTest limited to API automation and Allure reporting.
- Keep raw API wrappers in `api/platform/`.
- Keep pytest cases under `tests/<business_domain>/api/`.
- Keep reusable assertions in `infrastructure/assertions/`.
- Do not create code, tools, reports, or documents unrelated to API automation and Allure output.
- Do not create `__init__.py`; this project uses Python namespace packages and pytest importlib mode.
- Do not create hidden files or hidden directories in the project tree. The only allowed dot-prefixed path is Git metadata under `.git`; use non-hidden config names such as `config/env.<env>`.
- Do not modify `infrastructure/` unless the user explicitly asks.
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

Create local config:

```bash
cp config/env.example config/env.test
```

Real API tests need valid `BASE_URL` and optional `API_KEY` in
`config/env.<env>`.

## Useful Commands

Collect tests:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest --collect-only -q
```

Run all API tests:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest -v
```

Run a domain slice:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/kline/api -v
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/binance/api -v
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/coinglass/api -v
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/factor_data/api -v
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/open_interest/api -v
```

Generate and open Allure report:

```bash
allure generate ./allure-results -o ./allure-report --clean
allure open ./allure-report
```
