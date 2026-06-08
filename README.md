# QuestTest API 自动化测试框架

QuestTest 是一个单一职责项目：只负责数据中台接口自动化测试，并输出 Allure 报告。

## 项目边界

只保留：

- 平台接口请求封装
- pytest 接口自动化用例
- 接口响应结构、字段精度、时间戳、DQC 和基础金融逻辑断言
- Allure 测试结果与报告元数据

不放入：

- 与接口自动化和 Allure 报告无关的任何代码、文档、脚本或产物

## 目录结构

```text
QuestTest/
  api/
    base_api.py
    platform/                  # 平台接口请求封装
  config/                      # 环境配置
  data/                        # 接口测试参数数据
  infrastructure/
    assertions/                # DQC 和金融逻辑断言
    http/                      # HTTP 客户端和重试
  tests/
    binance/api/
    coinglass/api/
    factor_data/api/
    kline/api/
    open_interest/api/
  docs/                        # 接口自动化说明
  pytest.ini                   # pytest + Allure 配置
  requirements.txt
```

## 环境准备

推荐 Python：

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12
```

安装依赖：

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pip install -r requirements.txt
```

创建本地配置：

```bash
cp config/env.example config/env.test
```

配置 `config/env.<env>` 中的 `BASE_URL` 和可选 `API_KEY`。

## 常用命令

收集测试：

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest --collect-only -q
```

运行全部接口自动化测试并输出 Allure 原始结果：

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest -v
```

按业务域运行：

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/kline/api -v
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/binance/api -v
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/coinglass/api -v
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/factor_data/api -v
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/open_interest/api -v
```

切换环境：

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest -v --env=prod
```

生成并打开 Allure 报告：

```bash
allure generate ./allure-results -o ./allure-report --clean
allure open ./allure-report
```

## 维护规则

- 新接口请求封装放在 `api/platform/`。
- 新 pytest 用例放在 `tests/<business_domain>/api/`。
- 通用 HTTP 能力放在 `infrastructure/http/`。
- 通用断言放在 `infrastructure/assertions/`。
- 测试数据放在 `data/`。
- 不新增与接口自动化和 Allure 报告无关的目录或文件。
- 不创建 `__init__.py`；项目使用 Python namespace package 和 pytest importlib 模式。
- 不创建隐藏文件或隐藏目录；除 Git 仓库元数据 `.git` 外，项目文件不得使用点号开头命名。环境配置统一使用 `config/env.<env>`，不要使用 `.env`。
