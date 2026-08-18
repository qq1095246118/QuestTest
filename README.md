# QuestTest

QuestTest 是一个面向 HTTP/JSON 接口自动化的 Python 3.12 pytest 框架。项目架构、职责边界和后续改造规则以 [框架设计文档](docs/automation-test-framework-design.md) 为准。

## 分层结构

```text
tests/cases -> service -> api
                     -> db
tests/cases -> tools
api、db、service -> config
```

- `tests/`：可执行场景、静态测试数据和 pytest Fixture。
- `service/`：可复用的业务流程编排，不放测试断言。
- `api/`：资源或领域级 HTTP 请求封装，不放业务断言。
- `db/`：连接、事务、参数化查询和按实体组织的 Repository。
- `tools/`：无业务含义的断言、测试数据、时间和文件工具。
- `config/`：默认、测试和预发环境配置；敏感信息只通过环境变量注入。
- `reports/`：运行生成的 JUnit XML，不提交报告内容。

## 安装

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[test]'
```

## 配置

配置加载优先级为：`config/default.yaml` < `config/<环境>.yaml` < 环境变量。

```bash
set -a
source .env.example
set +a
export AUTOMATION_API_BASE_URL='https://test.example.com'
export AUTOMATION_DB_HOST='127.0.0.1'
export AUTOMATION_DB_NAME='automation_test'
export AUTOMATION_DB_USERNAME='automation_user'
export AUTOMATION_DB_PASSWORD='replace-with-secret'
```

真实密码、Token 和生产凭据不得写入 YAML、JSON、Python 代码或版本库。DB 写操作仅允许测试环境，并且测试必须清理自身创建的数据。

## 运行

```bash
.venv/bin/python -m pytest --env test --junitxml reports/junit.xml
.venv/bin/python scripts/run_tests.py --env test
.venv/bin/python scripts/run_tests.py --env test --marker smoke
```

当前示例全部使用 Mock HTTP 与临时 SQLite 文件，不会访问真实接口或数据库。后续接入真实业务时，按接口资源在 `api/` 建立封装，按业务能力在 `service/` 建立编排，按数据实体在 `db/` 建立 Repository，并在 `tests/cases/` 编写可执行场景。
