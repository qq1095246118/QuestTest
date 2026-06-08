# AI Generation Guide

QuestTest is only for API automation and Allure report output.

## Core Rules

1. Keep the project single-purpose.
   - Do not add code, documents, scripts, generated files, or top-level source areas unless they are necessary for API automation and Allure output.

2. Respect directory roles.
   - `api/platform/` holds raw platform API request wrappers.
   - `infrastructure/http/` holds HTTP client and retry behavior.
   - `infrastructure/assertions/` holds reusable DQC and financial logic assertions.
   - `tests/<business_domain>/api/` holds executable pytest API tests.
   - `data/` holds API test parameter data.
   - `docs/` holds API automation notes only.

3. Do not create forbidden project files.
   - Do not create `__init__.py`; the project uses Python namespace packages and pytest importlib mode.
   - Do not create hidden files or hidden directories. The only allowed dot-prefixed path is Git metadata under `.git`; environment config must use `config/env.<env>`, not `.env`.

4. Keep Allure metadata useful.
   - Prefer clear docstrings with case IDs and test purpose.
   - Use existing pytest markers when they describe the API test dimension.
   - Keep report metadata in `tests/conftest.py`.

5. Stay inside the documented API scope.
   - Add or change tests only for platform APIs that are already part of this framework or explicitly requested by the user.
   - Use reusable assertion helpers instead of bare status-code checks when validating financial data behavior.
