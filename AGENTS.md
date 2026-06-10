# Agent Guide

QuestTest is a single-purpose Python 3.12 pytest project.

Its only responsibilities are:

- factor-library API automation tests
- reusable HTTP/DB/comparison support for those tests
- Allure result metadata and report output

Anything unrelated to API automation and Allure output belongs outside
QuestTest.

## Project Layout

| Path | Role |
|---|---|
| `api/platform/` | Raw Factor Library API request wrappers. |
| `config/` | Environment settings loaded from `config/env.<env>`. |
| `service/common/http/` | Shared HTTP client and retry behavior. |
| `service/common/db/` | Shared read-only database and SSH tunnel helpers for API verification. |
| `service/<business_domain>/<api_or_resource>/` | Business-domain service helpers aligned with executable API tests. |
| `tests/<business_domain>/<api_or_resource>/` | Executable pytest API tests grouped by business domain and interface/resource module. |
| `docs/` | API automation notes only. |
| `pytest.ini` | pytest and Allure defaults. |

## Test Domains

```text
tests/
  factor_library/Auth/
  factor_library/Chat/
  factor_library/Runs/
  factor_library/factor/
  factor_library/Admin/
  factor_library/Approval/
  factor_library/FactorIC/
  factor_library/Quantitative_Trading/
```

## Hard Rules

- Keep QuestTest limited to API automation and Allure reporting.
- Keep raw API wrappers in `api/platform/`.
- Keep pytest cases under `tests/<business_domain>/<api_or_resource>/`.
- Organize executable pytest cases with traditional test classes: define `Test<BusinessObjectOrCapability>` classes first, then put `test_*` methods inside the class. Do not scatter top-level test functions in case files.
- Keep shared HTTP/DB helpers in `service/common/`.
- Keep business-specific service helpers under `service/<business_domain>/<api_or_resource>/`.
- Organize `api/` and `service/` code with classes. Put ordinary business/helper methods inside the matching class instead of scattering module-level `def` functions. Pytest `conftest.py` fixtures and hooks are the exception.
- Keep small request parameters directly in the pytest case file.
- Keep case files focused on executable pytest cases and final assertions.
- Move complex response parsing, API-vs-DB comparison, and upstream/downstream data preparation into the matching `service/<business_domain>/<api_or_resource>/` directory.
- Do not add line-by-line comments. Every `def` should have a docstring explaining what it does, what request/input parameters it receives, and what it returns.
- Do not create code, tools, reports, or documents unrelated to API automation and Allure output.
- Do not create `__init__.py`; this project uses Python namespace packages and pytest importlib mode.
- Do not create hidden files or hidden directories for QuestTest source,
  configuration, tests, tools, reports, or docs. The `.agents/` directory is
  allowed only for agent skills and vendored agent skill dependencies, and is
  outside the QuestTest API automation framework boundary.
- Do not modify `service/` unless the user explicitly asks.
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

Real API tests need valid `BASE_URL`, Factor Library login account, and
optional DB/SSH settings in `config/env.<env>`.

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
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/factor_library -v
```

Generate and open Allure report:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest -v --alluredir=./allure-results --clean-alluredir
allure generate ./allure-results -o ./allure-report --clean
allure open ./allure-report
```
