# QuestTest API 自动化测试框架

QuestTest 是一个单一职责项目：只负责因子库接口自动化测试，并输出 Allure 报告。

## 项目边界

只保留：

- 因子库接口请求封装
- pytest 接口自动化用例
- 接口响应结构、DB 一致性和上下游一致性的 service 比较支撑
- Allure 测试结果与报告元数据

不放入：

- 与接口自动化和 Allure 报告无关的任何代码、文档、脚本或产物

## 目录结构

```text
QuestTest/
  api/
    platform/                  # 因子库接口请求封装
  config/                      # 环境配置
  service/
    common/
      db/                      # 通用只读 DB 与 SSH tunnel 校验支撑
      http/                    # 通用 HTTP 客户端和重试
    factor_library/
      factors/                 # 因子库 factors 接口服务支撑
  tests/
    factor_library/
      Auth/                    # Auth 接口自动化用例
      Chat/                    # Chat 接口自动化用例
      Runs/                    # Runs 接口自动化用例
      factor/                  # factor 接口自动化用例
      Admin/                   # Admin 接口自动化用例
      Approval/                # Approval 接口自动化用例
      FactorIC/                # FactorIC 接口自动化用例
      Quantitative_Trading/    # Quantitative Trading 接口自动化用例
      common/                  # 公共 service、规则和 wrapper 单元测试
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

配置 `config/env.<env>` 中的 `BASE_URL`、因子库登录账号和只读 DB 连接信息。

## 常用命令

收集测试：

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest --collect-only -q
```

运行全部接口自动化测试：

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest -v
```

按业务域运行：

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/factor_library -v
```

切换环境：

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest -v --env=prod
```

生成并打开 Allure 报告：

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest -v --alluredir=./allure-results --clean-alluredir
allure generate ./allure-results -o ./allure-report --clean
allure open ./allure-report
```

## 维护规则

- 新接口请求封装放在 `api/platform/`。
- 新 pytest 用例放在 `tests/<business_domain>/<api_or_resource>/`，第一层是业务域，第二层是接口或资源模块，第三层是对应接口的可执行自动化用例文件。
- 测试用例文件使用传统 class 组织：先定义 `Test<业务对象或接口能力>` 类，再在类中定义 `test_*` 用例方法。
- 通用 HTTP、只读 DB 能力放在 `service/common/`。
- 业务接口专属服务支撑放在 `service/<business_domain>/<api_or_resource>/`，与 `tests/<business_domain>/<api_or_resource>/` 对齐。
- `api/` 和 `service/` 层使用 class 组织能力，普通业务方法放在对应类中，不在模块顶层散放 `def`；pytest `conftest.py` 中的 fixture/hook 除外。
- 少量请求参数直接写在对应 pytest 用例文件里，避免为少量场景额外拆分配置层。
- case 层只保留可执行用例和最终断言；复杂响应解析、接口与 DB 比较、上下游数据整理放在对应 `service/<business_domain>/<api_or_resource>/` 中。
- 不做每行注释；每个 `def` 用 docstring 说明用途、请求参数和返回值。
- 不新增与接口自动化和 Allure 报告无关的目录或文件。
- 不创建 `__init__.py`；项目使用 Python namespace package 和 pytest importlib 模式。
- 不创建隐藏文件或隐藏目录；`.git` 和用于 agent skills 的 `.agents/` 是例外。环境配置统一使用 `config/env.<env>`，不要使用 `.env`。
