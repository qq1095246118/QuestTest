# AI Generation Guide

This guide establishes the core rules for AI assistants working on this test framework. 
To ensure framework stability and strict quality standards, the following rules MUST be adhered to:

## Core Rules

1. **Respect Layer Boundaries**
   - `infrastructure/` holds low-level HTTP, database, and assertion foundations.
   - `infrastructure/` is protected infrastructure and should not be modified unless the user explicitly asks.
   - `api/` holds raw API request wrappers only.
   - `services/` holds intermediate logic, judgment, comparison, caching, and report data preparation.
   - `tools/` holds directly runnable utilities and temporary Python files.
   - `tests/` holds executable pytest test files only.

2. **Mandatory DQC & Logic Assertions**
   All generated API test cases MUST use reusable assertion helpers from `infrastructure/assertions/` or service-level validators.

3. **Strict Directory Roles & Structure**
   New raw API wrappers MUST go into `api/`.
   New reusable logic MUST go into `services/`.
   New executable tools MUST go into `tools/`.
   New pytest cases MUST go into `tests/`.
   Test data and parameterizations MUST stay in `data/`.

4. **Testing Scope Restriction**
   Currently, the testing scope is strictly limited to the core tables outlined in the Data Platform PDF documentation (e.g., `binance_1h_usdm_kline_raw`, `coinglass_open_interest_raw`, `dqc_issues`, etc.). Do not generate test cases for other external tables (like News, On-chain data, or Meme tokens) until their schemas and logic definitions are explicitly provided and supplemented to the framework.
