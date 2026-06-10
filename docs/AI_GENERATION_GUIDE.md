# AI Generation Guide

QuestTest is only for Factor Library API automation and Allure report output.

## Core Rules

1. Keep the project single-purpose.
   - Do not add code, documents, scripts, generated files, or top-level source areas unless they are necessary for API automation and Allure output.

2. Respect directory roles.
   - `api/platform/` holds raw Factor Library API request wrappers.
   - `service/common/` holds shared HTTP and read-only DB helpers.
   - `service/<business_domain>/<api_or_resource>/` holds business-specific service helpers aligned with executable API tests.
   - `tests/<business_domain>/<api_or_resource>/` holds executable pytest API tests. The first level is the business domain, the second level is the interface/resource module, and the third level is the executable test file.
   - Test files must use traditional class organization: define `Test<BusinessObjectOrCapability>` classes first, then place `test_*` case methods inside the class.
   - `api/` and `service/` files must use class organization for ordinary business/helper methods. Do not scatter module-level `def` functions outside pytest `conftest.py` fixtures and hooks.
   - `docs/` holds API automation notes only.
   - Keep small request parameters directly inside the relevant pytest case file instead of splitting them into extra data layers.
   - Keep case files focused on executable pytest cases and final assertions.
   - Move complex response parsing, API-vs-DB comparison, and upstream/downstream data preparation into the matching service directory.
   - Do not generate line-by-line comments. Every `def` should have a docstring explaining purpose, request/input parameters, and return value.

3. Do not create forbidden project files.
   - Do not create `__init__.py`; the project uses Python namespace packages and pytest importlib mode.
   - Do not create hidden files or hidden directories. Git metadata under `.git` and project-local agent skills under `.agents/` are the only allowed dot-prefixed paths; environment config must use `config/env.<env>`, not `.env`.

4. Keep Allure metadata useful.
   - Prefer clear docstrings with case IDs and test purpose.
   - Use existing pytest markers when they describe the API test dimension.
   - Keep report metadata in `tests/conftest.py`.

5. Stay inside the documented API scope.
   - Add or change tests only for Factor Library APIs that are already part of this framework or explicitly requested by the user.
   - Use service-layer comparison helpers that return mismatch lists, then assert the result in the pytest case.
