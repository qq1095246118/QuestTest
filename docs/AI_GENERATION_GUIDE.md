# AI Generation Guide

This guide establishes the core rules for AI assistants working on this test framework. 
To ensure framework stability and strict quality standards, the following rules MUST be adhered to:

## Core Rules

1. **Do Not Modify Core Base (`core/`)**  
   The files under the `core/` directory (`http_client.py`, `dqc_asserts.py`, `logic_asserts.py`) are critical infrastructural components. You must **NOT** modify or delete these files unless explicitly instructed by the QA Architect.

2. **Mandatory DQC & Logic Assertions**  
   All generated API test cases MUST utilize the unified assertion methods provided in `dqc_asserts.py` and `logic_asserts.py`. Generic `assert True` or basic equality checks are strictly prohibited for financial logic or data quality verification.

3. **Strict Directory Roles & Structure**  
   Do not mix concerns or create new root-level folders without permission.  
   - New API encapsulations MUST go into `api_services/`.  
   - New global fixtures MUST go into `tests/conftest.py`.  
   - Test data and parameterizations MUST be stored in the `data/` directory.

4. **Testing Scope Restriction**
   Currently, the testing scope is strictly limited to the core tables outlined in the Data Platform PDF documentation (e.g., `binance_1h_usdm_kline_raw`, `coinglass_open_interest_raw`, `dqc_issues`, etc.). Do not generate test cases for other external tables (like News, On-chain data, or Meme tokens) until their schemas and logic definitions are explicitly provided and supplemented to the framework.
