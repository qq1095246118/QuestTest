# Factor Library API Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付因子库第一版 XMind 接口用例结构文档，并实现传统 pytest 自动化的登录鉴权 + `GET /api/v1/factors` 因子列表接口验证。

**Architecture:** 用例结构文档放在 `docs/`，自动化代码按 QuestTest 现有分层组织：`api/platform/` 负责原始接口封装，`infrastructure/db/` 负责只读 MySQL 与 SSH tunnel，`infrastructure/assertions/` 负责接口/DB/上下游一致性断言，`tests/factor_library/api/` 负责编排 pytest 用例和 Allure 元数据。YAML 接口文档只作为前期参考，不参与运行时。

**Tech Stack:** Python 3.12, pytest, requests, allure-pytest, pydantic-settings, PyYAML, PyMySQL, sshtunnel, tenacity。

---

## Scope Check

已确认第一版只做一个可独立交付的子项目：因子库 XMind 结构文档 + 登录鉴权 + 因子列表接口自动化。写接口、全量补漏专项、正式环境执行、多账号权限矩阵不在本计划内。

## File Structure

本计划创建或修改以下文件：

- Create `docs/test_cases_factor_library_xmind.txt`  
  可直接复制进 XMind 的缩进结构文档，格式为 `模块 → 功能分类 → 用例名称 → 操作 → 结果`。

- Modify `requirements.txt`  
  增加 `pymysql` 和 `sshtunnel`，用于只读 DB 对账和 SSH tunnel。

- Modify `config/settings.py`  
  增加因子库账号、DB、SSH 配置字段。

- Modify `config/env.test`  
  写入测试环境 API、登录账号、DB、SSH 配置。

- Modify `config/env.example`  
  写入因子库配置示例，供新环境复制。

- Modify `pytest.ini`  
  增加 `factor_library_api` 和 `live_db` marker。

- Create `api/platform/auth_api.py`  
  封装 `/api/v1/auth/login`。

- Create `api/platform/factor_library_api.py`  
  封装因子库只读接口，第一版主测 `GET /api/v1/factors`。

- Create `infrastructure/db/mysql_client.py`  
  只读 MySQL client，禁止非 SELECT SQL。

- Create `infrastructure/db/ssh_tunnel.py`  
  按配置启用 SSH tunnel；未启用时直连 MySQL。

- Create `infrastructure/db/factor_library_queries.py`  
  将 `GET /api/v1/factors` 的查询参数映射为 DB 查询，并返回可对账结构。

- Create `infrastructure/assertions/factor_library_asserts.py`  
  响应结构、分页、排序、DB 对账、上下游一致性断言。

- Create `data/factor_library_api_cases.yaml`  
  第一版因子列表接口参数数据。

- Create `tests/factor_library/api/test_factor_library_settings_unit.py`  
  配置读取单元测试。

- Create `tests/factor_library/api/test_auth_api_unit.py`  
  AuthAPI 封装单元测试。

- Create `tests/factor_library/api/test_factor_library_api_unit.py`  
  FactorLibraryAPI 封装单元测试。

- Create `tests/factor_library/api/test_factor_library_db_unit.py`  
  DB 只读保护、查询构造单元测试。

- Create `tests/factor_library/api/test_factor_library_asserts_unit.py`  
  断言函数单元测试。

- Create `tests/factor_library/api/test_factor_list_api.py`  
  测试环境 live pytest 用例：登录、鉴权异常、因子列表、分页、排序、筛选、DB 对账、上下游一致性。

不要创建 `__init__.py`。

---

### Task 1: XMind 用例结构文档

**Files:**
- Create: `docs/test_cases_factor_library_xmind.txt`

- [ ] **Step 1: 创建 XMind 缩进文档**

Create `docs/test_cases_factor_library_xmind.txt` with this exact content:

```text
因子库接口用例
  登录鉴权
    登录
      AU-01 有效账号登录成功
        使用 haoran@gmail.com 请求 login，POST /api/v1/auth/login
          返回 success=true，data.token 不为空，data.user.email 与登录账号一致
      AU-02 错误密码登录失败
        使用正确邮箱和错误密码请求 login，POST /api/v1/auth/login
          返回明确的鉴权失败信息，不返回 token
    鉴权拦截
      AU-03 未带 token 查询因子列表
        不带 Authorization 请求 listFactors，GET /api/v1/factors
          返回未授权错误，不返回因子列表数据
      AU-04 使用无效 token 查询因子列表
        使用伪造 Authorization 请求 listFactors，GET /api/v1/factors
          返回未授权错误，不返回因子列表数据

  因子列表
    查询
      FA-01 查询因子列表成功
        使用有效 token 请求 listFactors，GET /api/v1/factors?page=1&limit=20
          返回 success=true，items 为因子列表，pagination 信息完整
          每条因子包含基础信息、factor_detail、themes
          接口返回数据与 DB 查询结果一致
    分页
      FA-02 查询第一页因子列表
        请求 listFactors，GET /api/v1/factors?page=1&limit=5
          返回 5 条以内数据，pagination.page=1，pagination.limit=5，pagination.total 与 DB 总数一致
      FA-03 切换分页大小
        请求 listFactors，GET /api/v1/factors?page=1&limit=50
          返回 50 条以内数据，当前页数据顺序与 DB 分页结果一致
      FA-04 查询第二页因子列表
        请求 listFactors，GET /api/v1/factors?page=2&limit=5
          返回第二页数据，与第一页数据不重复，顺序与 DB 分页结果一致
    排序
      FA-05 按更新时间升序查询
        请求 listFactors，GET /api/v1/factors?sort_by=updated_at&sort_order=asc
          返回数据按更新时间升序排列，顺序与 DB 查询结果一致
      FA-06 按更新时间降序查询
        请求 listFactors，GET /api/v1/factors?sort_by=updated_at&sort_order=desc
          返回数据按更新时间降序排列，顺序与 DB 查询结果一致
    筛选
      FA-07 按主题筛选因子
        请求 listFactors，GET /api/v1/factors?factor_theme=sentiment
          返回因子均归属于 sentiment 主题，接口 themes 与 DB 主题关系一致
      FA-08 按详情状态筛选因子
        请求 listFactors，GET /api/v1/factors?factor_detail_status=1
          返回因子 factor_detail.status 均为 1，接口数据与 DB 查询结果一致
      FA-09 按创建人筛选因子
        请求 listFactors，GET /api/v1/factors?created_by=System Admin
          返回因子 created_by 均为 System Admin，接口数据与 DB 查询结果一致
      FA-10 按操作人筛选因子
        请求 listFactors，GET /api/v1/factors?operator_by=System Admin
          返回因子 operator_by 均为 System Admin，接口数据与 DB 查询结果一致
    参数异常
      FA-11 page 为 0 查询因子列表
        请求 listFactors，GET /api/v1/factors?page=0&limit=20
          返回明确参数错误或自动修正后的合法分页结果，不返回 500
      FA-12 limit 超过最大值查询因子列表
        请求 listFactors，GET /api/v1/factors?page=1&limit=501
          返回明确参数错误或自动限制后的合法分页结果，不返回 500
      FA-13 sort_order 非法查询因子列表
        请求 listFactors，GET /api/v1/factors?sort_by=updated_at&sort_order=bad
          返回明确参数错误，不返回 500 或错误排序数据
    数据对账
      FA-DB-01 校验分页总数
        请求 listFactors 后按相同查询条件查询 DB factors 相关表
          pagination.total 与 DB 总数一致
      FA-DB-02 校验当前页顺序
        请求 listFactors 后按相同排序和分页条件查询 DB
          items 中 id 顺序与 DB 当前页 id 顺序一致
      FA-DB-03 校验因子基础字段
        请求 listFactors 后按返回 id 查询 DB factors 表
          id、serial_number、serial_prefix、factor_name、cn_name、level、max_level、child_factor_count、created_by、operator_by、created_at、updated_at 与 DB 一致
      FA-DB-04 校验因子详情字段
        请求 listFactors 后按 factor_id 查询 DB factors_details 表
          factor_detail 中 name、status、strategy_status、update_interval、hit_count 与 DB 一致
      FA-DB-05 校验主题归属
        请求 listFactors 后按 factor_id 查询 DB factor_theme_relations 和 themes
          接口返回 themes 数量、theme_key、theme_name、cn_name、status 与 DB 一致

  主题结构
    展示
      TH-01 查询主题列表
        使用有效 token 请求 listThemes，GET /api/v1/themes
          返回主题列表，主题基础字段与 DB themes 表一致
      TH-02 查询主题树
        使用有效 token 请求 listFactorThemeTree，GET /api/v1/factors/theme-tree
          返回主题到母因子、子因子的层级结构
    一致性
      TH-03 因子列表主题与主题列表一致
        对比 listFactors 返回的 themes 与 listThemes 返回的主题集合
          因子列表中的每个 theme_id 都能在主题列表中找到
      TH-04 因子列表主题与主题树一致
        对比 listFactors 返回的 themes 与 listFactorThemeTree 返回的主题节点
          因子列表中的每个主题归属都能在主题树中找到

  子因子
    查询
      SF-01 按母因子查询子因子
        使用有效 token 请求 listSubFactors，GET /api/v1/sub-factors?factor_id={factor_id}
          返回子因子列表，子因子均关联到请求的母因子
    一致性
      SF-02 校验母因子子因子数量
        对比 listFactors 中 child_factor_count 与 listSubFactors 返回数量
          子因子数量与接口和 DB 关系表一致

  IC/IR评价
    查询
      IC-01 查询母因子 IC 汇总
        使用有效 token 请求 getFactorICSummary，GET /api/v1/factor-ic/factors/{factor_id}/summary
          返回 IC/IR 汇总指标，指标字段结构完整
    一致性
      IC-02 因子列表评价字段与 IC 汇总一致
        当 listFactors 返回 IC/IR 字段时，对比 getFactorICSummary
          列表评价字段与 IC 汇总接口一致

  审批流转
    查询
      AP-01 查询审批列表
        使用有效 token 请求 listApprovals，GET /api/v1/approvals
          返回审批列表，状态字段为 pending、approved、rejected 或 cancelled
    状态
      AP-02 查询待审批记录
        请求 listApprovals，GET /api/v1/approvals?status=pending
          返回记录状态均为 pending

  权限异常
    鉴权
      PE-01 未登录访问因子库接口
        不带 Authorization 请求 listFactors，GET /api/v1/factors
          返回未授权错误
      PE-02 使用无效 token 访问因子库接口
        使用伪造 Authorization 请求 listFactors，GET /api/v1/factors
          返回未授权错误

  数据一致性
    接口与 DB
      DC-01 因子列表与 DB 一致
        对 listFactors 返回的当前页数据执行 DB 对账
          分页、顺序、基础字段、详情字段、主题归属全部一致
    上下游接口
      DC-02 因子列表与主题接口一致
        对比 listFactors、listThemes、listFactorThemeTree
          因子主题归属在上下游接口中一致
      DC-03 因子列表与子因子接口一致
        对比 listFactors、listSubFactors
          子因子数量和关联关系一致
      DC-04 因子列表与 IC 汇总接口一致
        当 listFactors 返回 IC/IR 字段时，对比 getFactorICSummary
          IC/IR 指标值一致
```

- [ ] **Step 2: 验证 XMind 文档格式**

Run:

```bash
python - <<'PY'
from pathlib import Path
path = Path("docs/test_cases_factor_library_xmind.txt")
text = path.read_text(encoding="utf-8")
assert "因子库接口用例" in text
assert "FA-DB-05 校验主题归属" in text
for line in text.splitlines():
    if line.strip():
        assert "\t" not in line
print("xmind structure ok")
PY
```

Expected:

```text
xmind structure ok
```

- [ ] **Step 3: Commit**

Run:

```bash
git add docs/test_cases_factor_library_xmind.txt
git commit -m "docs: add factor library xmind cases"
```

Expected: commit succeeds and only `docs/test_cases_factor_library_xmind.txt` is included.

---

### Task 2: 配置、依赖和 pytest marker

**Files:**
- Modify: `requirements.txt`
- Modify: `config/settings.py`
- Modify: `config/env.test`
- Modify: `config/env.example`
- Modify: `pytest.ini`
- Test: `tests/factor_library/api/test_factor_library_settings_unit.py`

- [ ] **Step 1: 写配置读取失败测试**

Create `tests/factor_library/api/test_factor_library_settings_unit.py`:

```python
from __future__ import annotations

from pathlib import Path

from config.settings import Settings


def test_factor_library_settings_load_from_env_file(tmp_path: Path):
    env_file = tmp_path / "env.test"
    env_file.write_text(
        "\n".join(
            [
                "ENV=test",
                "BASE_URL=https://test-factor-backend.questvector.ai",
                "FACTOR_EMAIL=haoran@gmail.com",
                "FACTOR_PASSWORD=Aa%@#haoran",
                "FACTOR_DB_HOST=43.167.190.122",
                "FACTOR_DB_PORT=3306",
                "FACTOR_DB_NAME=factor_db",
                "FACTOR_DB_USER=factor_app",
                "FACTOR_DB_PASSWORD=-RL1Zivb6wIzf4CmqJp6KQ6p",
                "FACTOR_SSH_ENABLED=true",
                "FACTOR_SSH_HOST=43.167.190.122",
                "FACTOR_SSH_PORT=22",
                "FACTOR_SSH_USER=appview",
                "FACTOR_SSH_KEY_PATH=/Users/wrh/.ssh/id_rsa",
                "FACTOR_SSH_PASSWORD=woxiangni.",
            ]
        ),
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.base_url == "https://test-factor-backend.questvector.ai"
    assert settings.factor_email == "haoran@gmail.com"
    assert settings.factor_password == "Aa%@#haoran"
    assert settings.factor_db_host == "43.167.190.122"
    assert settings.factor_db_port == 3306
    assert settings.factor_db_name == "factor_db"
    assert settings.factor_db_user == "factor_app"
    assert settings.factor_db_password == "-RL1Zivb6wIzf4CmqJp6KQ6p"
    assert settings.factor_ssh_enabled is True
    assert settings.factor_ssh_host == "43.167.190.122"
    assert settings.factor_ssh_port == 22
    assert settings.factor_ssh_user == "appview"
    assert settings.factor_ssh_key_path == "/Users/wrh/.ssh/id_rsa"
    assert settings.factor_ssh_password == "woxiangni."
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/factor_library/api/test_factor_library_settings_unit.py -q
```

Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'factor_email'`.

- [ ] **Step 3: 更新 requirements**

Modify `requirements.txt` by appending:

```text
pymysql==1.1.1
sshtunnel==0.4.0
```

The final file must contain:

```text
pytest>=8.2,<9
requests>=2.32.2,<3
pyyaml==6.0.1
allure-pytest>=2.16.0,<3
jsonschema==4.21.1
tenacity==8.2.3
pydantic>=2.7.0
pydantic-settings>=2.2.0
python-dotenv==1.0.1
pymysql==1.1.1
sshtunnel==0.4.0
```

- [ ] **Step 4: 更新 Settings**

Modify `config/settings.py` to:

```python
import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


CONFIG_DIR = Path(__file__).resolve().parent
ENV_FILE = CONFIG_DIR / f"env.{os.getenv('TEST_ENV', 'test')}"


class Settings(BaseSettings):
    env: str = "test"
    base_url: str = ""
    api_key: str = ""

    factor_email: str = ""
    factor_password: str = ""

    factor_db_host: str = ""
    factor_db_port: int = 3306
    factor_db_name: str = ""
    factor_db_user: str = ""
    factor_db_password: str = ""

    factor_ssh_enabled: bool = False
    factor_ssh_host: str = ""
    factor_ssh_port: int = 22
    factor_ssh_user: str = ""
    factor_ssh_key_path: str = ""
    factor_ssh_password: str = ""

    # Dynamically load config/env.<env> based on the selected test environment.
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
```

- [ ] **Step 5: 更新 config/env.test**

Modify `config/env.test` to:

```text
ENV=test
BASE_URL=https://test-factor-backend.questvector.ai
API_KEY=test_key_example

FACTOR_EMAIL=haoran@gmail.com
FACTOR_PASSWORD=Aa%@#haoran

FACTOR_DB_HOST=43.167.190.122
FACTOR_DB_PORT=3306
FACTOR_DB_NAME=factor_db
FACTOR_DB_USER=factor_app
FACTOR_DB_PASSWORD=-RL1Zivb6wIzf4CmqJp6KQ6p

FACTOR_SSH_ENABLED=true
FACTOR_SSH_HOST=43.167.190.122
FACTOR_SSH_PORT=22
FACTOR_SSH_USER=appview
FACTOR_SSH_KEY_PATH=/Users/wrh/.ssh/id_rsa
FACTOR_SSH_PASSWORD=woxiangni.
```

- [ ] **Step 6: 更新 config/env.example**

Modify `config/env.example` to:

```text
ENV=test
BASE_URL=https://test-factor-backend.questvector.ai
API_KEY=test_key_example

FACTOR_EMAIL=haoran@gmail.com
FACTOR_PASSWORD=Aa%@#haoran

FACTOR_DB_HOST=43.167.190.122
FACTOR_DB_PORT=3306
FACTOR_DB_NAME=factor_db
FACTOR_DB_USER=factor_app
FACTOR_DB_PASSWORD=-RL1Zivb6wIzf4CmqJp6KQ6p

FACTOR_SSH_ENABLED=true
FACTOR_SSH_HOST=43.167.190.122
FACTOR_SSH_PORT=22
FACTOR_SSH_USER=appview
FACTOR_SSH_KEY_PATH=/Users/wrh/.ssh/id_rsa
FACTOR_SSH_PASSWORD=woxiangni.
```

- [ ] **Step 7: 更新 pytest marker**

Modify `pytest.ini` marker section so it includes:

```ini
    factor_library_api: Run Factor Library platform API tests
    live_db: Run live DB-backed API consistency tests
```

Do not remove existing markers.

- [ ] **Step 8: Run settings test**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/factor_library/api/test_factor_library_settings_unit.py -q
```

Expected:

```text
1 passed
```

- [ ] **Step 9: Commit**

Run:

```bash
git add requirements.txt config/settings.py config/env.test config/env.example pytest.ini tests/factor_library/api/test_factor_library_settings_unit.py
git commit -m "chore: add factor library test configuration"
```

Expected: commit succeeds.

---

### Task 3: AuthAPI 登录封装

**Files:**
- Create: `api/platform/auth_api.py`
- Test: `tests/factor_library/api/test_auth_api_unit.py`

- [ ] **Step 1: Write failing tests**

Create `tests/factor_library/api/test_auth_api_unit.py`:

```python
from __future__ import annotations

from types import SimpleNamespace

from api.platform.auth_api import AuthAPI


def test_login_uses_configured_credentials(monkeypatch):
    calls = {}

    def fake_request(method, url, **kwargs):
        calls["method"] = method
        calls["url"] = url
        calls["kwargs"] = kwargs
        return SimpleNamespace(status_code=200, json=lambda: {"success": True, "data": {"token": "abc"}})

    monkeypatch.setattr("api.platform.auth_api.HTTPClient.request", fake_request)
    monkeypatch.setattr("api.platform.auth_api.settings.base_url", "https://test-factor-backend.questvector.ai")
    monkeypatch.setattr("api.platform.auth_api.settings.factor_email", "haoran@gmail.com")
    monkeypatch.setattr("api.platform.auth_api.settings.factor_password", "Aa%@#haoran")

    response = AuthAPI().login()

    assert response.status_code == 200
    assert calls["method"] == "POST"
    assert calls["url"] == "https://test-factor-backend.questvector.ai/api/v1/auth/login"
    assert calls["kwargs"]["headers"] == {"Content-Type": "application/json"}
    assert calls["kwargs"]["json"] == {"email": "haoran@gmail.com", "password": "Aa%@#haoran"}


def test_login_accepts_explicit_credentials(monkeypatch):
    calls = {}

    def fake_request(method, url, **kwargs):
        calls["kwargs"] = kwargs
        return SimpleNamespace(status_code=200, json=lambda: {"success": True, "data": {"token": "abc"}})

    monkeypatch.setattr("api.platform.auth_api.HTTPClient.request", fake_request)
    monkeypatch.setattr("api.platform.auth_api.settings.base_url", "https://test-factor-backend.questvector.ai")

    AuthAPI().login(email="user@example.com", password="secret")

    assert calls["kwargs"]["json"] == {"email": "user@example.com", "password": "secret"}
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/factor_library/api/test_auth_api_unit.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'api.platform.auth_api'`.

- [ ] **Step 3: Implement AuthAPI**

Create `api/platform/auth_api.py`:

```python
"""因子库鉴权 API 调用封装。

本模块只负责拼接请求参数并发起 HTTP 调用，不做业务断言。
"""

from __future__ import annotations

from config.settings import settings
from infrastructure.http.http_client import HTTPClient


class AuthAPI:
    def __init__(self):
        self.base_url = settings.base_url.rstrip("/")
        self.headers = {"Content-Type": "application/json"}

    def post(self, endpoint: str, json: dict | None = None):
        url = f"{self.base_url}{endpoint}"
        return HTTPClient.request("POST", url, headers=self.headers, json=json)

    def login(self, email: str | None = None, password: str | None = None):
        return self.post(
            "/api/v1/auth/login",
            json={
                "email": email or settings.factor_email,
                "password": password or settings.factor_password,
            },
        )
```

- [ ] **Step 4: Run tests**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/factor_library/api/test_auth_api_unit.py -q
```

Expected:

```text
2 passed
```

- [ ] **Step 5: Commit**

Run:

```bash
git add api/platform/auth_api.py tests/factor_library/api/test_auth_api_unit.py
git commit -m "feat: add factor auth api wrapper"
```

Expected: commit succeeds.

---

### Task 4: FactorLibraryAPI 只读接口封装

**Files:**
- Create: `api/platform/factor_library_api.py`
- Test: `tests/factor_library/api/test_factor_library_api_unit.py`

- [ ] **Step 1: Write failing tests**

Create `tests/factor_library/api/test_factor_library_api_unit.py`:

```python
from __future__ import annotations

from types import SimpleNamespace

from api.platform.factor_library_api import FactorLibraryAPI


def test_list_factors_sends_clean_query_params(monkeypatch):
    calls = {}

    def fake_request(method, url, **kwargs):
        calls["method"] = method
        calls["url"] = url
        calls["kwargs"] = kwargs
        return SimpleNamespace(status_code=200, json=lambda: {"success": True})

    monkeypatch.setattr("api.platform.factor_library_api.HTTPClient.request", fake_request)
    monkeypatch.setattr("api.platform.factor_library_api.settings.base_url", "https://test-factor-backend.questvector.ai")

    FactorLibraryAPI(token="token-1").list_factors(
        page=1,
        limit=5,
        factor_theme="sentiment",
        status=None,
        sort_by="updated_at",
        sort_order="asc",
    )

    assert calls["method"] == "GET"
    assert calls["url"] == "https://test-factor-backend.questvector.ai/api/v1/factors"
    assert calls["kwargs"]["headers"]["Authorization"] == "Bearer token-1"
    assert calls["kwargs"]["params"] == {
        "page": 1,
        "limit": 5,
        "factor_theme": "sentiment",
        "sort_by": "updated_at",
        "sort_order": "asc",
    }


def test_factor_library_auxiliary_routes(monkeypatch):
    urls = []

    def fake_request(method, url, **kwargs):
        urls.append((method, url, kwargs.get("params")))
        return SimpleNamespace(status_code=200, json=lambda: {"success": True})

    monkeypatch.setattr("api.platform.factor_library_api.HTTPClient.request", fake_request)
    monkeypatch.setattr("api.platform.factor_library_api.settings.base_url", "https://test-factor-backend.questvector.ai")

    api = FactorLibraryAPI(token="token-1")
    api.list_themes()
    api.list_factor_theme_tree()
    api.list_sub_factors(factor_id=615)
    api.get_factor_ic_summary(factor_id=615, ic_scope="time_series", time_window="1h")

    assert urls == [
        ("GET", "https://test-factor-backend.questvector.ai/api/v1/themes", {}),
        ("GET", "https://test-factor-backend.questvector.ai/api/v1/factors/theme-tree", {}),
        ("GET", "https://test-factor-backend.questvector.ai/api/v1/sub-factors", {"factor_id": 615}),
        (
            "GET",
            "https://test-factor-backend.questvector.ai/api/v1/factor-ic/factors/615/summary",
            {"ic_scope": "time_series", "time_window": "1h"},
        ),
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/factor_library/api/test_factor_library_api_unit.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'api.platform.factor_library_api'`.

- [ ] **Step 3: Implement FactorLibraryAPI**

Create `api/platform/factor_library_api.py`:

```python
"""因子库原始 API 调用封装。

本模块只负责拼接请求参数并发起 HTTP 调用，不做业务判断或断言。
"""

from __future__ import annotations

from typing import Any

from config.settings import settings
from infrastructure.http.http_client import HTTPClient


class FactorLibraryAPI:
    def __init__(self, token: str | None = None):
        self.base_url = settings.base_url.rstrip("/")
        self.headers = {"Content-Type": "application/json"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def get(self, endpoint: str, params: dict[str, Any] | None = None):
        url = f"{self.base_url}{endpoint}"
        return HTTPClient.request("GET", url, headers=self.headers, params=_clean(params))

    def list_factors(
        self,
        page: Any = None,
        limit: Any = None,
        factor_theme: Any = None,
        time_window: Any = None,
        created_by: Any = None,
        created_from: Any = None,
        created_to: Any = None,
        operator_by: Any = None,
        operated_from: Any = None,
        operated_to: Any = None,
        status: Any = None,
        factor_detail_status: Any = None,
        sort_by: Any = None,
        sort_order: Any = None,
    ):
        return self.get(
            "/api/v1/factors",
            {
                "page": page,
                "limit": limit,
                "factor_theme": factor_theme,
                "time_window": time_window,
                "created_by": created_by,
                "created_from": created_from,
                "created_to": created_to,
                "operator_by": operator_by,
                "operated_from": operated_from,
                "operated_to": operated_to,
                "status": status,
                "factor_detail_status": factor_detail_status,
                "sort_by": sort_by,
                "sort_order": sort_order,
            },
        )

    def list_themes(self, theme_key: Any = None, theme_name: Any = None):
        return self.get("/api/v1/themes", {"theme_key": theme_key, "theme_name": theme_name})

    def list_factor_theme_tree(self):
        return self.get("/api/v1/factors/theme-tree")

    def list_sub_factors(
        self,
        page: Any = None,
        limit: Any = None,
        sub_factor_name: Any = None,
        factor_id: Any = None,
        factor_detail_status: Any = None,
        sort_by: Any = None,
        sort_order: Any = None,
    ):
        return self.get(
            "/api/v1/sub-factors",
            {
                "page": page,
                "limit": limit,
                "sub_factor_name": sub_factor_name,
                "factor_id": factor_id,
                "factor_detail_status": factor_detail_status,
                "sort_by": sort_by,
                "sort_order": sort_order,
            },
        )

    def get_factor_ic_summary(
        self,
        factor_id: int,
        ic_scope: Any = None,
        time_window: Any = None,
    ):
        return self.get(
            f"/api/v1/factor-ic/factors/{factor_id}/summary",
            {"ic_scope": ic_scope, "time_window": time_window},
        )


def _clean(params: dict[str, Any] | None) -> dict[str, Any]:
    if not params:
        return {}
    return {key: value for key, value in params.items() if value is not None}
```

- [ ] **Step 4: Run tests**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/factor_library/api/test_factor_library_api_unit.py -q
```

Expected:

```text
2 passed
```

- [ ] **Step 5: Commit**

Run:

```bash
git add api/platform/factor_library_api.py tests/factor_library/api/test_factor_library_api_unit.py
git commit -m "feat: add factor library api wrapper"
```

Expected: commit succeeds.

---

### Task 5: 只读 DB client 与 SSH tunnel

**Files:**
- Create: `infrastructure/db/mysql_client.py`
- Create: `infrastructure/db/ssh_tunnel.py`
- Test: `tests/factor_library/api/test_factor_library_db_unit.py`

- [ ] **Step 1: Write failing DB client tests**

Create `tests/factor_library/api/test_factor_library_db_unit.py`:

```python
from __future__ import annotations

from types import SimpleNamespace

import pytest

from infrastructure.db.mysql_client import ReadOnlyMySQLClient, ensure_select_only
from infrastructure.db.ssh_tunnel import DatabaseEndpoint, open_database_endpoint


def test_ensure_select_only_accepts_select_and_with():
    ensure_select_only("SELECT * FROM factors")
    ensure_select_only("  WITH latest AS (SELECT 1) SELECT * FROM latest")


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE factors SET cn_name='x'",
        "DELETE FROM factors",
        "INSERT INTO factors(id) VALUES (1)",
        "DROP TABLE factors",
        "SELECT * FROM factors; DELETE FROM factors",
    ],
)
def test_ensure_select_only_rejects_mutating_sql(sql):
    with pytest.raises(ValueError, match="Only SELECT statements are allowed"):
        ensure_select_only(sql)


def test_fetch_all_uses_dict_cursor(monkeypatch):
    executed = {}

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params):
            executed["sql"] = sql
            executed["params"] = params

        def fetchall(self):
            return [{"id": 1, "factor_name": "momentum"}]

    class FakeConnection:
        def cursor(self):
            return FakeCursor()

        def close(self):
            executed["closed"] = True

    def fake_connect(**kwargs):
        executed["connect_kwargs"] = kwargs
        return FakeConnection()

    monkeypatch.setattr("infrastructure.db.mysql_client.pymysql.connect", fake_connect)

    client = ReadOnlyMySQLClient(
        host="127.0.0.1",
        port=3306,
        user="factor_app",
        password="secret",
        database="factor_db",
    )

    rows = client.fetch_all("SELECT * FROM factors WHERE id=%(id)s", {"id": 1})

    assert rows == [{"id": 1, "factor_name": "momentum"}]
    assert executed["sql"] == "SELECT * FROM factors WHERE id=%(id)s"
    assert executed["params"] == {"id": 1}
    assert executed["connect_kwargs"]["cursorclass"] is not None


def test_open_database_endpoint_direct(monkeypatch):
    fake_settings = SimpleNamespace(
        factor_ssh_enabled=False,
        factor_db_host="43.167.190.122",
        factor_db_port=3306,
        factor_ssh_host="",
        factor_ssh_port=22,
        factor_ssh_user="",
        factor_ssh_key_path="",
        factor_ssh_password="",
    )

    with open_database_endpoint(fake_settings) as endpoint:
        assert endpoint == DatabaseEndpoint(host="43.167.190.122", port=3306)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/factor_library/api/test_factor_library_db_unit.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'infrastructure.db'`.

- [ ] **Step 3: Implement ReadOnlyMySQLClient**

Create `infrastructure/db/mysql_client.py`:

```python
"""只读 MySQL 客户端，用于接口返回值与 DB 数据对账。"""

from __future__ import annotations

import re
from typing import Any

import pymysql
from pymysql.cursors import DictCursor

from config.settings import settings

MUTATING_SQL_RE = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|replace|grant|revoke)\b",
    re.IGNORECASE,
)


def ensure_select_only(sql: str) -> None:
    normalized = sql.strip().lower()
    if ";" in normalized.rstrip(";"):
        raise ValueError("Only SELECT statements are allowed for DB assertions.")
    if not (normalized.startswith("select") or normalized.startswith("with")):
        raise ValueError("Only SELECT statements are allowed for DB assertions.")
    if MUTATING_SQL_RE.search(normalized):
        raise ValueError("Only SELECT statements are allowed for DB assertions.")


class ReadOnlyMySQLClient:
    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
    ):
        self.host = host
        self.port = int(port)
        self.user = user
        self.password = password
        self.database = database
        self._connection = None

    @classmethod
    def from_settings(cls, host: str | None = None, port: int | None = None):
        return cls(
            host=host or settings.factor_db_host,
            port=port or settings.factor_db_port,
            user=settings.factor_db_user,
            password=settings.factor_db_password,
            database=settings.factor_db_name,
        )

    def connect(self):
        if self._connection is None:
            self._connection = pymysql.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                database=self.database,
                charset="utf8mb4",
                cursorclass=DictCursor,
                autocommit=True,
                connect_timeout=10,
                read_timeout=30,
                write_timeout=30,
            )
        return self._connection

    def fetch_all(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        ensure_select_only(sql)
        connection = self.connect()
        with connection.cursor() as cursor:
            cursor.execute(sql, params or {})
            return list(cursor.fetchall())

    def fetch_one(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        rows = self.fetch_all(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
```

- [ ] **Step 4: Implement SSH endpoint context**

Create `infrastructure/db/ssh_tunnel.py`:

```python
"""SSH tunnel 管理，用于访问只读 MySQL。"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class DatabaseEndpoint:
    host: str
    port: int


@contextmanager
def open_database_endpoint(settings) -> Iterator[DatabaseEndpoint]:
    if not settings.factor_ssh_enabled:
        yield DatabaseEndpoint(host=settings.factor_db_host, port=int(settings.factor_db_port))
        return

    from sshtunnel import SSHTunnelForwarder

    ssh_kwargs = {
        "ssh_username": settings.factor_ssh_user,
        "remote_bind_address": (settings.factor_db_host, int(settings.factor_db_port)),
    }
    if settings.factor_ssh_key_path:
        ssh_kwargs["ssh_pkey"] = settings.factor_ssh_key_path
    if settings.factor_ssh_password:
        ssh_kwargs["ssh_password"] = settings.factor_ssh_password

    server = SSHTunnelForwarder(
        (settings.factor_ssh_host, int(settings.factor_ssh_port)),
        **ssh_kwargs,
    )
    server.start()
    try:
        yield DatabaseEndpoint(host="127.0.0.1", port=int(server.local_bind_port))
    finally:
        server.stop()
```

- [ ] **Step 5: Run DB unit tests**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/factor_library/api/test_factor_library_db_unit.py -q
```

Expected:

```text
8 passed
```

- [ ] **Step 6: Commit**

Run:

```bash
git add infrastructure/db/mysql_client.py infrastructure/db/ssh_tunnel.py tests/factor_library/api/test_factor_library_db_unit.py
git commit -m "feat: add read-only factor db client"
```

Expected: commit succeeds.

---

### Task 6: 因子列表 DB 查询映射

**Files:**
- Create: `infrastructure/db/factor_library_queries.py`
- Modify: `tests/factor_library/api/test_factor_library_db_unit.py`

- [ ] **Step 1: Add failing query mapping tests**

Append to `tests/factor_library/api/test_factor_library_db_unit.py`:

```python
from infrastructure.db.factor_library_queries import FactorListQuery, fetch_factor_list_db_page


def test_fetch_factor_list_db_page_builds_total_and_rows_queries():
    calls = []

    class FakeClient:
        def fetch_one(self, sql, params):
            calls.append(("one", sql, params))
            return {"total": 1}

        def fetch_all(self, sql, params):
            calls.append(("all", sql, params))
            if "FROM factors f" in sql:
                return [
                    {
                        "id": 615,
                        "serial_number": "PF_copy_400925",
                        "serial_prefix": "PF",
                        "factor_name": "long_short_ratio_copy_400925",
                        "cn_name": "多空比_copy_400925",
                        "factor_tags": "[\"情绪类\", \"多空比\"]",
                        "level": 1,
                        "max_level": 2,
                        "child_factor_count": 87,
                        "created_by": "System Admin",
                        "created_by_uid": 1,
                        "operator_by": "System Admin",
                        "operator_by_uid": 1,
                        "metadata": "{}",
                        "created_at": "2026-06-06 14:00:01",
                        "updated_at": "2026-06-06 14:00:01",
                    }
                ]
            if "FROM factors_details" in sql:
                return [
                    {
                        "id": 8699,
                        "factor_id": 615,
                        "is_sub_factor_id": 0,
                        "serial_number": "PF_copy_400925",
                        "name": "PF_copy_400925",
                        "description": "",
                        "data_source": "",
                        "calc_function": "",
                        "calc_logic": "",
                        "params": "",
                        "explanation": "",
                        "update_interval": 0,
                        "hit_count": 0,
                        "strategy_status": 0,
                        "status": 1,
                        "created_at": "2026-06-06 14:00:01",
                        "updated_at": "2026-06-06 14:00:01",
                    }
                ]
            if "FROM factor_theme_relations" in sql:
                return [
                    {
                        "factor_id": 615,
                        "id": 12,
                        "theme_key": "sentiment",
                        "theme_name": "sentiment",
                        "cn_name": "情绪类",
                        "theme_tags": "",
                        "max_level": 2,
                        "factor_count": 5,
                        "sub_factor_count": 192,
                        "status": 2,
                        "created_by": "AI-Agent",
                        "created_by_uid": 0,
                        "operator_by": "AI-Agent",
                        "operator_by_uid": 0,
                        "created_at": "2026-04-19 16:17:56",
                        "updated_at": "2026-05-19 17:00:01",
                    }
                ]
            return []

    page = fetch_factor_list_db_page(
        FakeClient(),
        FactorListQuery(page=1, limit=5, factor_theme="sentiment", sort_by="updated_at", sort_order="asc"),
    )

    assert page["pagination"]["total"] == 1
    assert page["items"][0]["id"] == 615
    assert page["items"][0]["factor_detail"]["status"] == 1
    assert page["items"][0]["themes"][0]["theme_key"] == "sentiment"
    assert calls[0][2]["factor_theme"] == "sentiment"
    assert calls[1][2]["limit"] == 5
    assert calls[1][2]["offset"] == 0
```

- [ ] **Step 2: Run query test to verify it fails**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/factor_library/api/test_factor_library_db_unit.py::test_fetch_factor_list_db_page_builds_total_and_rows_queries -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'infrastructure.db.factor_library_queries'`.

- [ ] **Step 3: Implement factor list query mapping**

Create `infrastructure/db/factor_library_queries.py`:

```python
"""因子库接口与 DB 对账查询。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FactorListQuery:
    page: int = 1
    limit: int = 20
    factor_theme: str | None = None
    created_by: str | None = None
    operator_by: str | None = None
    factor_detail_status: int | None = None
    sort_by: str | None = None
    sort_order: str | None = None

    @property
    def offset(self) -> int:
        return max(self.page - 1, 0) * self.limit


SORT_COLUMNS = {
    "id": "f.id",
    "created_at": "f.created_at",
    "updated_at": "f.updated_at",
    "factor_name": "f.factor_name",
    "cn_name": "f.cn_name",
}


def fetch_factor_list_db_page(client, query: FactorListQuery) -> dict[str, Any]:
    where_sql, params = _build_where(query)
    total_sql = f"""
        SELECT COUNT(DISTINCT f.id) AS total
        FROM factors f
        LEFT JOIN factors_details fd
          ON fd.factor_id = f.id AND fd.is_sub_factor_id = 0
        LEFT JOIN factor_theme_relations ftr
          ON ftr.factor_id = f.id
        LEFT JOIN themes t
          ON t.id = ftr.theme_id
        {where_sql}
    """
    total_row = client.fetch_one(total_sql, params) or {"total": 0}

    sort_column = SORT_COLUMNS.get(query.sort_by or "id", "f.id")
    sort_order = "ASC" if str(query.sort_order).lower() == "asc" else "DESC"
    row_params = dict(params)
    row_params.update({"limit": int(query.limit), "offset": int(query.offset)})
    rows_sql = f"""
        SELECT
          f.id,
          f.serial_number,
          f.serial_prefix,
          f.factor_name,
          f.cn_name,
          f.factor_tags,
          f.level,
          f.max_level,
          f.child_factor_count,
          f.created_by,
          f.created_by_uid,
          f.operator_by,
          f.operator_by_uid,
          f.metadata,
          f.created_at,
          f.updated_at
        FROM factors f
        LEFT JOIN factors_details fd
          ON fd.factor_id = f.id AND fd.is_sub_factor_id = 0
        LEFT JOIN factor_theme_relations ftr
          ON ftr.factor_id = f.id
        LEFT JOIN themes t
          ON t.id = ftr.theme_id
        {where_sql}
        GROUP BY f.id
        ORDER BY {sort_column} {sort_order}, f.id {sort_order}
        LIMIT %(limit)s OFFSET %(offset)s
    """
    factor_rows = client.fetch_all(rows_sql, row_params)
    factor_ids = [int(row["id"]) for row in factor_rows]

    details = _fetch_details(client, factor_ids)
    themes = _fetch_themes(client, factor_ids)
    items = []
    for row in factor_rows:
        factor_id = int(row["id"])
        item = dict(row)
        item["factor_detail"] = details.get(factor_id)
        item["themes"] = themes.get(factor_id, [])
        items.append(item)

    return {
        "pagination": {
            "page": query.page,
            "limit": query.limit,
            "total": int(total_row.get("total") or 0),
        },
        "items": items,
    }


def _build_where(query: FactorListQuery) -> tuple[str, dict[str, Any]]:
    clauses = []
    params: dict[str, Any] = {}
    if query.factor_theme:
        clauses.append("t.theme_key = %(factor_theme)s")
        params["factor_theme"] = query.factor_theme
    if query.created_by:
        clauses.append("f.created_by = %(created_by)s")
        params["created_by"] = query.created_by
    if query.operator_by:
        clauses.append("f.operator_by = %(operator_by)s")
        params["operator_by"] = query.operator_by
    if query.factor_detail_status is not None:
        clauses.append("fd.status = %(factor_detail_status)s")
        params["factor_detail_status"] = int(query.factor_detail_status)
    if not clauses:
        return "", params
    return "WHERE " + " AND ".join(clauses), params


def _id_params(factor_ids: list[int]) -> tuple[str, dict[str, Any]]:
    params = {f"id_{index}": factor_id for index, factor_id in enumerate(factor_ids)}
    placeholders = ", ".join(f"%({key})s" for key in params)
    return placeholders, params


def _fetch_details(client, factor_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not factor_ids:
        return {}
    placeholders, params = _id_params(factor_ids)
    rows = client.fetch_all(
        f"""
        SELECT
          id,
          factor_id,
          is_sub_factor_id,
          serial_number,
          name,
          description,
          data_source,
          calc_function,
          calc_logic,
          params,
          explanation,
          update_interval,
          hit_count,
          strategy_status,
          status,
          created_at,
          updated_at
        FROM factors_details
        WHERE is_sub_factor_id = 0
          AND factor_id IN ({placeholders})
        """,
        params,
    )
    return {int(row["factor_id"]): row for row in rows}


def _fetch_themes(client, factor_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not factor_ids:
        return {}
    placeholders, params = _id_params(factor_ids)
    rows = client.fetch_all(
        f"""
        SELECT
          ftr.factor_id,
          t.id,
          t.theme_key,
          t.theme_name,
          t.cn_name,
          t.theme_tags,
          t.max_level,
          t.factor_count,
          t.sub_factor_count,
          t.status,
          t.created_by,
          t.created_by_uid,
          t.operator_by,
          t.operator_by_uid,
          t.created_at,
          t.updated_at
        FROM factor_theme_relations ftr
        JOIN themes t ON t.id = ftr.theme_id
        WHERE ftr.factor_id IN ({placeholders})
        ORDER BY ftr.factor_id DESC, t.id ASC
        """,
        params,
    )
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        factor_id = int(row.pop("factor_id"))
        grouped[factor_id].append(row)
    return grouped
```

- [ ] **Step 4: Run DB unit tests**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/factor_library/api/test_factor_library_db_unit.py -q
```

Expected:

```text
9 passed
```

- [ ] **Step 5: Commit**

Run:

```bash
git add infrastructure/db/factor_library_queries.py tests/factor_library/api/test_factor_library_db_unit.py
git commit -m "feat: map factor list db queries"
```

Expected: commit succeeds.

---

### Task 7: 因子库断言函数

**Files:**
- Create: `infrastructure/assertions/factor_library_asserts.py`
- Test: `tests/factor_library/api/test_factor_library_asserts_unit.py`

- [ ] **Step 1: Write failing assertion tests**

Create `tests/factor_library/api/test_factor_library_asserts_unit.py`:

```python
from __future__ import annotations

import pytest

from infrastructure.assertions.factor_library_asserts import (
    assert_factor_list_matches_db,
    assert_factor_list_shape,
    assert_success_body,
)


def _api_body():
    return {
        "success": True,
        "data": {
            "items": [
                {
                    "id": 615,
                    "serial_number": "PF_copy_400925",
                    "serial_prefix": "PF",
                    "factor_name": "long_short_ratio_copy_400925",
                    "cn_name": "多空比_copy_400925",
                    "factor_tags": "[\"情绪类\", \"多空比\"]",
                    "level": 1,
                    "max_level": 2,
                    "child_factor_count": 87,
                    "created_by": "System Admin",
                    "created_by_uid": 1,
                    "operator_by": "System Admin",
                    "operator_by_uid": 1,
                    "created_at": "2026-06-06T14:00:01Z",
                    "updated_at": "2026-06-06T14:00:01Z",
                    "factor_detail": {
                        "id": 8699,
                        "factor_id": 615,
                        "is_sub_factor_id": False,
                        "serial_number": "PF_copy_400925",
                        "name": "PF_copy_400925",
                        "update_interval": 0,
                        "hit_count": 0,
                        "strategy_status": 0,
                        "status": 1,
                    },
                    "themes": [
                        {
                            "id": 12,
                            "theme_key": "sentiment",
                            "theme_name": "sentiment",
                            "cn_name": "情绪类",
                            "status": 2,
                        }
                    ],
                }
            ],
            "pagination": {"page": 1, "limit": 5, "total": 1, "total_pages": 1},
        },
    }


def _db_page():
    return {
        "pagination": {"page": 1, "limit": 5, "total": 1},
        "items": [
            {
                "id": 615,
                "serial_number": "PF_copy_400925",
                "serial_prefix": "PF",
                "factor_name": "long_short_ratio_copy_400925",
                "cn_name": "多空比_copy_400925",
                "factor_tags": "[\"情绪类\", \"多空比\"]",
                "level": 1,
                "max_level": 2,
                "child_factor_count": 87,
                "created_by": "System Admin",
                "created_by_uid": 1,
                "operator_by": "System Admin",
                "operator_by_uid": 1,
                "created_at": "2026-06-06 14:00:01",
                "updated_at": "2026-06-06 14:00:01",
                "factor_detail": {
                    "id": 8699,
                    "factor_id": 615,
                    "is_sub_factor_id": 0,
                    "serial_number": "PF_copy_400925",
                    "name": "PF_copy_400925",
                    "update_interval": 0,
                    "hit_count": 0,
                    "strategy_status": 0,
                    "status": 1,
                },
                "themes": [
                    {
                        "id": 12,
                        "theme_key": "sentiment",
                        "theme_name": "sentiment",
                        "cn_name": "情绪类",
                        "status": 2,
                    }
                ],
            }
        ],
    }


def test_assert_success_body_accepts_success_true():
    assert_success_body(_api_body())


def test_assert_factor_list_shape_accepts_real_shape():
    assert_factor_list_shape(_api_body())


def test_assert_factor_list_matches_db_accepts_matching_data():
    assert_factor_list_matches_db(_api_body(), _db_page())


def test_assert_factor_list_matches_db_fails_on_field_mismatch():
    body = _api_body()
    body["data"]["items"][0]["child_factor_count"] = 88

    with pytest.raises(AssertionError, match="child_factor_count"):
        assert_factor_list_matches_db(body, _db_page())
```

- [ ] **Step 2: Run assertion tests to verify they fail**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/factor_library/api/test_factor_library_asserts_unit.py -q
```

Expected: FAIL with `ImportError` for missing assertion functions.

- [ ] **Step 3: Implement assertion functions**

Create `infrastructure/assertions/factor_library_asserts.py`:

```python
"""因子库接口断言。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


FACTOR_FIELDS = [
    "id",
    "serial_number",
    "serial_prefix",
    "factor_name",
    "cn_name",
    "factor_tags",
    "level",
    "max_level",
    "child_factor_count",
    "created_by",
    "created_by_uid",
    "operator_by",
    "operator_by_uid",
]

TIME_FIELDS = ["created_at", "updated_at"]

DETAIL_FIELDS = [
    "id",
    "factor_id",
    "serial_number",
    "name",
    "update_interval",
    "hit_count",
    "strategy_status",
    "status",
]

THEME_FIELDS = ["id", "theme_key", "theme_name", "cn_name", "status"]


def assert_success_body(body: dict[str, Any]) -> None:
    assert body.get("success") is True, f"success should be true, body={body}"
    assert "data" in body, "success response must include data"


def assert_factor_list_shape(body: dict[str, Any]) -> None:
    assert_success_body(body)
    data = body["data"]
    assert isinstance(data, dict)
    assert isinstance(data.get("items"), list)
    assert isinstance(data.get("pagination"), dict)
    pagination = data["pagination"]
    for field in ("page", "limit", "total"):
        assert field in pagination, f"pagination missing {field}"
        assert isinstance(pagination[field], int), f"pagination.{field} should be int"
    for item in data["items"]:
        for field in ("id", "serial_number", "factor_name", "cn_name", "factor_detail", "themes"):
            assert field in item, f"factor item missing {field}: {item}"
        assert isinstance(item["themes"], list)
        assert isinstance(item["factor_detail"], dict)


def assert_factor_list_matches_db(api_body: dict[str, Any], db_page: dict[str, Any]) -> None:
    assert_factor_list_shape(api_body)
    api_data = api_body["data"]
    api_pagination = api_data["pagination"]
    db_pagination = db_page["pagination"]
    assert api_pagination["total"] == db_pagination["total"], (
        f"pagination.total mismatch: api={api_pagination['total']}, db={db_pagination['total']}"
    )

    api_items = api_data["items"]
    db_items = db_page["items"]
    assert len(api_items) == len(db_items), f"item count mismatch: api={len(api_items)}, db={len(db_items)}"
    for index, (api_item, db_item) in enumerate(zip(api_items, db_items)):
        assert api_item["id"] == db_item["id"], (
            f"item order mismatch at index={index}: api_id={api_item['id']}, db_id={db_item['id']}"
        )
        _assert_factor_fields(api_item, db_item)
        _assert_detail_fields(api_item.get("factor_detail"), db_item.get("factor_detail"), api_item["id"])
        _assert_themes(api_item.get("themes", []), db_item.get("themes", []), api_item["id"])


def assert_theme_ids_exist_in_theme_list(factor_body: dict[str, Any], themes_body: dict[str, Any]) -> None:
    assert_factor_list_shape(factor_body)
    assert_success_body(themes_body)
    theme_items = themes_body["data"]
    if isinstance(theme_items, dict) and "items" in theme_items:
        theme_items = theme_items["items"]
    known_ids = {int(theme["id"]) for theme in theme_items}
    for factor in factor_body["data"]["items"]:
        for theme in factor.get("themes", []):
            assert int(theme["id"]) in known_ids, (
                f"theme_id={theme['id']} returned by listFactors but missing from listThemes"
            )


def _assert_factor_fields(api_item: dict[str, Any], db_item: dict[str, Any]) -> None:
    factor_id = api_item["id"]
    for field in FACTOR_FIELDS:
        assert api_item.get(field) == db_item.get(field), (
            f"factor_id={factor_id}, field={field}, api={api_item.get(field)!r}, db={db_item.get(field)!r}"
        )
    for field in TIME_FIELDS:
        assert _normalize_time(api_item.get(field)) == _normalize_time(db_item.get(field)), (
            f"factor_id={factor_id}, field={field}, api={api_item.get(field)!r}, db={db_item.get(field)!r}"
        )


def _assert_detail_fields(api_detail: dict[str, Any] | None, db_detail: dict[str, Any] | None, factor_id: int) -> None:
    assert api_detail is not None, f"factor_id={factor_id} missing factor_detail in api"
    assert db_detail is not None, f"factor_id={factor_id} missing factor_detail in db"
    for field in DETAIL_FIELDS:
        assert api_detail.get(field) == db_detail.get(field), (
            f"factor_id={factor_id}, factor_detail.{field}, api={api_detail.get(field)!r}, db={db_detail.get(field)!r}"
        )
    api_is_sub = bool(api_detail.get("is_sub_factor_id"))
    db_is_sub = bool(db_detail.get("is_sub_factor_id"))
    assert api_is_sub == db_is_sub, (
        f"factor_id={factor_id}, factor_detail.is_sub_factor_id, api={api_is_sub}, db={db_is_sub}"
    )


def _assert_themes(api_themes: list[dict[str, Any]], db_themes: list[dict[str, Any]], factor_id: int) -> None:
    assert len(api_themes) == len(db_themes), (
        f"factor_id={factor_id}, themes count mismatch: api={len(api_themes)}, db={len(db_themes)}"
    )
    api_by_id = {int(theme["id"]): theme for theme in api_themes}
    db_by_id = {int(theme["id"]): theme for theme in db_themes}
    assert set(api_by_id) == set(db_by_id), (
        f"factor_id={factor_id}, theme ids mismatch: api={sorted(api_by_id)}, db={sorted(db_by_id)}"
    )
    for theme_id, api_theme in api_by_id.items():
        db_theme = db_by_id[theme_id]
        for field in THEME_FIELDS:
            assert api_theme.get(field) == db_theme.get(field), (
                f"factor_id={factor_id}, theme_id={theme_id}, field={field}, "
                f"api={api_theme.get(field)!r}, db={db_theme.get(field)!r}"
            )


def _normalize_time(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        value = value.astimezone(timezone.utc)
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    text = str(value).replace(" ", "T")
    if "." in text:
        text = text.split(".", 1)[0]
    if text.endswith("+00:00"):
        text = text[:-6] + "Z"
    if not text.endswith("Z"):
        text = f"{text}Z"
    return text
```

- [ ] **Step 4: Run assertion tests**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/factor_library/api/test_factor_library_asserts_unit.py -q
```

Expected:

```text
4 passed
```

- [ ] **Step 5: Commit**

Run:

```bash
git add infrastructure/assertions/factor_library_asserts.py tests/factor_library/api/test_factor_library_asserts_unit.py
git commit -m "feat: add factor library assertions"
```

Expected: commit succeeds.

---

### Task 8: 因子列表 live pytest 用例

**Files:**
- Create: `data/factor_library_api_cases.yaml`
- Create: `tests/factor_library/api/test_factor_list_api.py`

- [ ] **Step 1: Create data file**

Create `data/factor_library_api_cases.yaml`:

```yaml
factor_list:
  default:
    page: 1
    limit: 5
  page_two:
    page: 2
    limit: 5
  sort_updated_at_asc:
    page: 1
    limit: 5
    sort_by: updated_at
    sort_order: asc
  sort_updated_at_desc:
    page: 1
    limit: 5
    sort_by: updated_at
    sort_order: desc
  invalid_page_zero:
    page: 0
    limit: 20
  invalid_limit_too_large:
    page: 1
    limit: 501
  invalid_sort_order:
    page: 1
    limit: 20
    sort_by: updated_at
    sort_order: bad
```

- [ ] **Step 2: Write live tests**

Create `tests/factor_library/api/test_factor_list_api.py`:

```python
from __future__ import annotations

from pathlib import Path

import allure
import pytest
import yaml
from requests.exceptions import HTTPError

from api.platform.auth_api import AuthAPI
from api.platform.factor_library_api import FactorLibraryAPI
from config.settings import settings
from infrastructure.assertions.factor_library_asserts import (
    assert_factor_list_matches_db,
    assert_factor_list_shape,
    assert_success_body,
    assert_theme_ids_exist_in_theme_list,
)
from infrastructure.db.factor_library_queries import FactorListQuery, fetch_factor_list_db_page
from infrastructure.db.mysql_client import ReadOnlyMySQLClient
from infrastructure.db.ssh_tunnel import open_database_endpoint


DATA_FILE = Path("data/factor_library_api_cases.yaml")


def _load_case(name: str) -> dict:
    data = yaml.safe_load(DATA_FILE.read_text(encoding="utf-8"))
    return data["factor_list"][name]


@pytest.fixture(scope="module")
def token() -> str:
    if not settings.base_url:
        pytest.skip("Factor Library API BASE_URL is not configured.")
    if not settings.factor_email or not settings.factor_password:
        pytest.skip("Factor Library login account is not configured.")

    response = AuthAPI().login()
    body = response.json()
    assert_success_body(body)
    token_value = body["data"].get("token")
    assert token_value, f"login response missing token: {body}"
    return token_value


@pytest.fixture(scope="module")
def factor_api(token: str) -> FactorLibraryAPI:
    return FactorLibraryAPI(token=token)


@pytest.fixture(scope="module")
def db_client():
    required = [
        settings.factor_db_host,
        settings.factor_db_name,
        settings.factor_db_user,
        settings.factor_db_password,
    ]
    if not all(required):
        pytest.skip("Factor Library DB config is not complete.")

    with open_database_endpoint(settings) as endpoint:
        client = ReadOnlyMySQLClient.from_settings(host=endpoint.host, port=endpoint.port)
        try:
            yield client
        finally:
            client.close()


@allure.title("AU-01 有效账号登录成功")
@pytest.mark.factor_library_api
def test_au_01_login_success():
    """
    Case ID: AU-01
    测试目的: 使用有效账号登录因子库后端，返回 token 和用户信息。
    """
    response = AuthAPI().login()
    assert response.status_code == 200
    body = response.json()
    assert_success_body(body)
    assert body["data"]["token"]
    assert body["data"]["user"]["email"] == settings.factor_email


@allure.title("AU-02 错误密码登录失败")
@pytest.mark.factor_library_api
def test_au_02_login_wrong_password_fails():
    """
    Case ID: AU-02
    测试目的: 使用错误密码登录时返回鉴权失败，不返回 token。
    """
    try:
        response = AuthAPI().login(email=settings.factor_email, password="wrong-password-for-api-test")
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code in {400, 401, 403}
    body = response.json() if response.content else {}
    assert "token" not in str(body).lower()


@allure.title("AU-03 未带 token 查询因子列表")
@pytest.mark.factor_library_api
def test_au_03_list_factors_without_token_is_unauthorized():
    """
    Case ID: AU-03
    测试目的: 未带 Authorization 访问因子列表时返回未授权错误。
    """
    try:
        response = FactorLibraryAPI().list_factors(page=1, limit=5)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code in {401, 403}


@allure.title("AU-04 使用无效 token 查询因子列表")
@pytest.mark.factor_library_api
def test_au_04_list_factors_invalid_token_is_unauthorized():
    """
    Case ID: AU-04
    测试目的: 使用伪造 token 访问因子列表时返回未授权错误。
    """
    try:
        response = FactorLibraryAPI(token="invalid-token").list_factors(page=1, limit=5)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code in {401, 403}


@allure.title("FA-01 查询因子列表成功并与 DB 一致")
@pytest.mark.factor_library_api
@pytest.mark.live_db
def test_fa_01_list_factors_success_matches_db(factor_api, db_client):
    """
    Case ID: FA-01
    测试目的: 查询第一页因子列表，验证响应结构、分页和 DB 数据一致。
    """
    params = _load_case("default")
    response = factor_api.list_factors(**params)
    body = response.json()
    assert_factor_list_shape(body)

    db_page = fetch_factor_list_db_page(db_client, FactorListQuery(**params))
    assert_factor_list_matches_db(body, db_page)


@allure.title("FA-03 查询第二页因子列表并验证分页不重复")
@pytest.mark.factor_library_api
@pytest.mark.live_db
def test_fa_03_page_two_does_not_overlap_first_page(factor_api, db_client):
    """
    Case ID: FA-03
    测试目的: 查询第二页因子列表，验证与第一页不重复且当前页顺序与 DB 一致。
    """
    first_params = _load_case("default")
    second_params = _load_case("page_two")

    first_body = factor_api.list_factors(**first_params).json()
    second_body = factor_api.list_factors(**second_params).json()
    assert_factor_list_shape(first_body)
    assert_factor_list_shape(second_body)

    first_ids = {item["id"] for item in first_body["data"]["items"]}
    second_ids = {item["id"] for item in second_body["data"]["items"]}
    assert first_ids.isdisjoint(second_ids)

    db_page = fetch_factor_list_db_page(db_client, FactorListQuery(**second_params))
    assert_factor_list_matches_db(second_body, db_page)


@allure.title("FA-05 按更新时间升序查询因子列表")
@pytest.mark.factor_library_api
@pytest.mark.live_db
def test_fa_05_sort_updated_at_asc_matches_db(factor_api, db_client):
    """
    Case ID: FA-05
    测试目的: 按 updated_at 升序查询，验证接口顺序与 DB 一致。
    """
    params = _load_case("sort_updated_at_asc")
    body = factor_api.list_factors(**params).json()
    db_page = fetch_factor_list_db_page(db_client, FactorListQuery(**params))
    assert_factor_list_matches_db(body, db_page)


@allure.title("FA-06 按更新时间降序查询因子列表")
@pytest.mark.factor_library_api
@pytest.mark.live_db
def test_fa_06_sort_updated_at_desc_matches_db(factor_api, db_client):
    """
    Case ID: FA-06
    测试目的: 按 updated_at 降序查询，验证接口顺序与 DB 一致。
    """
    params = _load_case("sort_updated_at_desc")
    body = factor_api.list_factors(**params).json()
    db_page = fetch_factor_list_db_page(db_client, FactorListQuery(**params))
    assert_factor_list_matches_db(body, db_page)


@allure.title("FA-07 按主题筛选因子")
@pytest.mark.factor_library_api
@pytest.mark.live_db
def test_fa_07_filter_by_theme_matches_db(factor_api, db_client):
    """
    Case ID: FA-07
    测试目的: 使用当前第一页真实返回的 theme_key 做主题筛选，验证接口和 DB 一致。
    """
    seed_body = factor_api.list_factors(page=1, limit=5).json()
    assert_factor_list_shape(seed_body)
    first_theme = seed_body["data"]["items"][0]["themes"][0]["theme_key"]

    params = {"page": 1, "limit": 5, "factor_theme": first_theme}
    body = factor_api.list_factors(**params).json()
    db_page = fetch_factor_list_db_page(db_client, FactorListQuery(**params))
    assert_factor_list_matches_db(body, db_page)


@allure.title("FA-08 按详情状态筛选因子")
@pytest.mark.factor_library_api
@pytest.mark.live_db
def test_fa_08_filter_by_factor_detail_status_matches_db(factor_api, db_client):
    """
    Case ID: FA-08
    测试目的: 使用 factor_detail_status=1 筛选因子，验证接口和 DB 一致。
    """
    params = {"page": 1, "limit": 5, "factor_detail_status": 1}
    body = factor_api.list_factors(**params).json()
    db_page = fetch_factor_list_db_page(db_client, FactorListQuery(**params))
    assert_factor_list_matches_db(body, db_page)


@allure.title("FA-11 page 为 0 查询因子列表不返回 500")
@pytest.mark.factor_library_api
def test_fa_11_page_zero_does_not_return_500(factor_api):
    """
    Case ID: FA-11
    测试目的: page=0 时接口返回明确参数错误或合法修正结果，不返回 500。
    """
    params = _load_case("invalid_page_zero")
    try:
        response = factor_api.list_factors(**params)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500


@allure.title("FA-12 limit 超过最大值查询因子列表不返回 500")
@pytest.mark.factor_library_api
def test_fa_12_limit_too_large_does_not_return_500(factor_api):
    """
    Case ID: FA-12
    测试目的: limit=501 时接口返回明确参数错误或合法限制结果，不返回 500。
    """
    params = _load_case("invalid_limit_too_large")
    try:
        response = factor_api.list_factors(**params)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500


@allure.title("FA-13 sort_order 非法查询因子列表不返回错误排序数据")
@pytest.mark.factor_library_api
def test_fa_13_invalid_sort_order_does_not_return_500(factor_api):
    """
    Case ID: FA-13
    测试目的: sort_order 非法时接口返回明确参数错误，不返回 500。
    """
    params = _load_case("invalid_sort_order")
    try:
        response = factor_api.list_factors(**params)
    except HTTPError as exc:
        assert exc.response is not None
        response = exc.response

    assert response.status_code < 500


@allure.title("DC-02 因子列表与主题列表一致")
@pytest.mark.factor_library_api
def test_dc_02_factor_themes_exist_in_theme_list(factor_api):
    """
    Case ID: DC-02
    测试目的: 因子列表返回的主题在主题列表接口中存在。
    """
    factor_body = factor_api.list_factors(page=1, limit=5).json()
    themes_body = factor_api.list_themes().json()
    assert_theme_ids_exist_in_theme_list(factor_body, themes_body)
```

- [ ] **Step 3: Run live tests without DB first**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/factor_library/api/test_factor_list_api.py -m "factor_library_api and not live_db" -v --env=test
```

Expected: login/auth/parameter/upstream tests run. Network must reach `https://test-factor-backend.questvector.ai`. Tests should PASS or expose real API behavior differences.

- [ ] **Step 4: Run live DB-backed tests**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/factor_library/api/test_factor_list_api.py -m live_db -v --env=test
```

Expected: DB-backed tests connect through configured SSH tunnel and either PASS or fail with a concrete field mismatch such as `factor_id=615, field=child_factor_count, api=87, db=84`.

- [ ] **Step 5: Commit**

Run:

```bash
git add data/factor_library_api_cases.yaml tests/factor_library/api/test_factor_list_api.py
git commit -m "test: add factor list live api checks"
```

Expected: commit succeeds.

---

### Task 9: Verification and final cleanup

**Files:**
- Review all files changed by Tasks 1-8.

- [ ] **Step 1: Run focused unit tests**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest \
  tests/factor_library/api/test_factor_library_settings_unit.py \
  tests/factor_library/api/test_auth_api_unit.py \
  tests/factor_library/api/test_factor_library_api_unit.py \
  tests/factor_library/api/test_factor_library_db_unit.py \
  tests/factor_library/api/test_factor_library_asserts_unit.py \
  -q
```

Expected:

```text
18 passed
```

- [ ] **Step 2: Run live API tests**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/factor_library/api/test_factor_list_api.py -v --env=test
```

Expected: tests pass against test API and DB, or fail with concrete API/DB mismatch evidence.

- [ ] **Step 3: Collect factor_library slice**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/factor_library/api --collect-only -q
```

Expected: pytest collects all factor library tests without import errors.

- [ ] **Step 4: Run diff check**

Run:

```bash
git diff --check
```

Expected: no whitespace errors.

- [ ] **Step 5: Review git status**

Run:

```bash
git status --short
```

Expected: only intended task files are modified or untracked. Existing user changes such as `AGENTS.md`, `.agents/`, `.idea/`, and the Word docs may still appear and must not be reverted.

- [ ] **Step 6: Final commit if Task 9 changed files**

If Task 9 only ran verification and did not edit files, skip this commit. If Task 9 made fixes, run:

```bash
git add docs/test_cases_factor_library_xmind.txt requirements.txt config/settings.py config/env.test config/env.example pytest.ini api/platform/auth_api.py api/platform/factor_library_api.py infrastructure/db/mysql_client.py infrastructure/db/ssh_tunnel.py infrastructure/db/factor_library_queries.py infrastructure/assertions/factor_library_asserts.py data/factor_library_api_cases.yaml tests/factor_library/api/test_factor_library_settings_unit.py tests/factor_library/api/test_auth_api_unit.py tests/factor_library/api/test_factor_library_api_unit.py tests/factor_library/api/test_factor_library_db_unit.py tests/factor_library/api/test_factor_library_asserts_unit.py tests/factor_library/api/test_factor_list_api.py
git commit -m "test: verify factor library api automation"
```

Expected: commit succeeds if there were verification fixes.

---

## Self-Review Checklist

Spec coverage:

- XMind 缩进结构文档：Task 1。
- 传统 pytest 自动化，不做 YAML 驱动：Tasks 3-8。
- 测试环境 API 和账号配置：Task 2。
- DB 和 SSH 配置：Task 2。
- `POST /api/v1/auth/login`：Task 3 and Task 8。
- `GET /api/v1/factors`：Task 4 and Task 8。
- 只读 DB client：Task 5。
- 因子列表 DB 对账：Task 6 and Task 7。
- 上下游主题一致性：Task 7 and Task 8。
- Allure metadata：Task 8。
- 不创建 `__init__.py`：File Structure section states this explicitly.

Placeholder scan:

- No `TBD`.
- No `TODO`.
- No `implement later`.
- No omitted code step for files created in the plan.

Type consistency:

- `FactorLibraryAPI.list_factors()` params match `FactorListQuery` for implemented filters: `page`, `limit`, `factor_theme`, `created_by`, `operator_by`, `factor_detail_status`, `sort_by`, `sort_order`.
- `fetch_factor_list_db_page()` returns `{"pagination": ..., "items": ...}`, matching `assert_factor_list_matches_db()`.
- `ReadOnlyMySQLClient.fetch_one()` and `fetch_all()` signatures match query usage.
