# 自动化测试用例框架设计文档

## QuestTest 落地约束

- 本文档是 QuestTest 的架构基线；后续新增、修改和重构必须遵守本文档的目录职责与依赖方向。
- 项目采用 Python 3.12、pytest、HTTP/JSON 和 JUnit XML 作为首版技术实现；这些实现选择不改变本文档的分层原则。
- 测试环境允许 DB 层执行数据准备、事务和清理；生产环境不得配置写入凭据，也不得执行写操作。
- 敏感信息只从环境变量注入，不能写入配置文件、测试数据或版本库。
- 当实际业务需求与本文档冲突时，必须先确认规则，再修改框架或实现。

## 1. 文档信息

- 版本：1.0
- 状态：初版设计
- 用途：作为代码生成 AI 的实现依据
- 目标：构建一个分层清晰、易维护、可复用的 API 自动化测试框架

## 2. 设计范围

本框架用于接口自动化测试，并支持在测试过程中访问数据库进行数据准备、状态校验和数据清理。

本设计默认以下技术形态，但实现时可以替换：

- 语言：Python 3.11+
- 测试运行器：pytest
- 接口协议：HTTP/JSON
- 数据库：关系型数据库
- 报告：JUnit XML 或 HTML，具体插件可按项目选择

如果实际项目使用 Java、JavaScript 或其他测试框架，只替换语言和运行器相关实现，不改变目录职责和依赖方向。

## 3. 核心设计原则

1. 测试用例只描述测试场景，不直接拼接复杂 URL、不散落 SQL、不承载完整业务流程。
2. Service 按业务能力组织，可被多个测试用例复用，不以 `case_001` 之类的用例编号命名。
3. API 层只封装接口协议和接口文档中的端点语义，不包含业务断言。
4. DB 层只负责数据库连接、查询、事务和数据访问，不负责业务编排。
5. 解耦不等于万能方法。底层客户端可以通用，但上层 API 和 Service 方法应有清晰、稳定的参数语义。
6. 运行生成物与人工维护文档分离。
7. 敏感信息不能写入代码、测试数据或提交到版本库。

## 4. 目录结构

```text
automation-test/
├── tests/                      # 测试入口层：用例、Fixture、测试数据
│   ├── cases/                  # 实际测试用例，文件名使用 test_*.py
│   ├── data/                   # 静态测试数据，如 JSON、YAML、CSV
│   └── conftest.py             # pytest Fixture、前置/后置和环境初始化
│
├── service/                    # 业务逻辑层：编排多个 API 或 DB 操作
│   └── <domain>_service.py      # 按实际业务领域创建，不预置具体领域
│
├── api/                        # 接口封装层：封装原始接口调用
│   ├── client.py               # 通用 HTTP 请求、超时、鉴权、重试
│   └── <domain>_api.py          # 按实际接口领域创建
│
├── db/                         # 数据库访问层：查询、事务和数据清理
│   ├── client.py               # 连接、事务、参数化查询执行
│   ├── repository.py           # 语义化数据访问方法；规模增大后按实体拆分
│   └── sql/                    # 复杂或复用 SQL，可选
│
├── tools/                      # 无业务含义的公共工具
│   ├── assertions.py           # 可复用的通用断言
│   ├── data_factory.py         # 动态测试数据生成
│   ├── time_utils.py
│   └── file_utils.py
│
├── config/                     # 环境和运行配置
│   ├── test.yaml
│   └── staging.yaml
│
├── docs/                       # 架构、运行和用例编写文档
├── reports/                    # 自动生成的测试报告，加入 .gitignore
├── scripts/                    # 执行测试、清理数据、生成报告的脚本
├── README.md
└── pyproject.toml              # 依赖、pytest 和代码质量工具配置
```

初版不需要额外引入 `core`、`domain`、`ports`、`adapters` 等目录。只有在多个实现、跨项目复用或依赖倒置确实产生需求时再增加。

## 5. 各层职责

### 5.1 `tests`

负责：

- 定义测试场景和测试数据
- 调用 Service 或 API
- 执行简单断言
- 组织测试前置、后置和数据清理
- 添加 pytest 标记，例如 smoke、regression、integration

禁止：

- 直接拼接复杂接口 URL
- 在测试用例中散落 SQL
- 把可复用的业务流程复制到多个 Case 中

### 5.2 `service`

负责：

- 用业务语言封装可复用动作
- 编排多个 API 和 DB 操作
- 处理业务流程中的前置条件和结果转换
- 返回结果、领域数据或明确异常

禁止：

- 依赖某个具体测试用例
- 直接写测试断言
- 把所有参数都设计成无语义的 `dict`

示例：按实际业务定义可复用动作，例如创建有效实体、准备可支付资源或完成一次业务状态流转。这些方法应按业务领域命名，可以被多个 Case 调用。

### 5.3 `api`

负责：

- 封装 HTTP 方法、路径、请求头、鉴权和请求体
- 处理超时、重试、序列化和基础错误映射
- 按资源或业务领域提供明确的接口方法

推荐：

```python
resource_api.create_resource(name="example")
resource_api.get_resource(resource_id)
```

不推荐让所有上层直接使用万能入口：

```python
request(method, url, params, data)
```

通用 `request` 可以存在于 `api/client.py`，但不应成为业务层的主要调用方式。

### 5.4 `db`

负责：

- 数据库连接和连接释放
- 参数化查询
- 事务提交、回滚和清理
- 提供语义化的数据访问方法

推荐：

```python
repository.find_by_id(entity_id)
repository.delete_by_id(entity_id)
```

禁止在测试用例中直接拼接 SQL；禁止在 Repository 中编排完整业务流程。

### 5.5 `tools`

只放通用、低业务耦合的能力，例如时间处理、文件处理、随机数据生成、重试等待和通用断言。

带有用户、订单、支付等领域含义的方法应放入 `service`、`api` 或 `db`，不能无限堆入 `tools`。

## 6. 依赖方向

```text
tests/cases ──> service ──> api
      │             └──────> db
      └──────────────> tools

api、db、service ──> config
```

约束：

- `api` 和 `db` 不能反向依赖 `service` 或 `tests`
- `service` 不能感知具体 Case 名称
- `tests` 可以直接调用 `api` 或 `db`，不强制所有测试都经过 `service`
- 不允许为了复用而形成循环依赖

## 7. 测试执行流程

典型业务流程测试遵循以下顺序：

1. 读取环境配置
2. Fixture 初始化客户端和数据库连接
3. 准备测试数据
4. 调用 Service 编排业务动作
5. 必要时通过 DB 查询验证持久化状态
6. 执行断言
7. 清理测试数据
8. 输出日志和测试报告

典型 Case 示例：

```python
def test_business_action_returns_expected_status():
    entity = domain_service.create_valid_entity()
    result = domain_service.perform_action(entity.id)

    assert result.status == "EXPECTED"
    assert repository.find_by_id(entity.id) is not None
```

### 7.1 组合因子真实链路的额外约束

组合因子台的 Worker 回调合约测试和真实 Agent 端到端测试必须分开标记、分开准备数据：

1. Worker 合约测试可以使用测试环境的兼容认领接口和 `simulation_mode=true`，只验证接口契约与数据库持久化，不得把模拟结果称为真实计算通过。
2. 真实流程必须使用已经存在且对当前账号可见的 Agent。查询 Agent 列表时同时携带 Factor JWT 和与 JWT 对应的 `X-User-Id`；测试代码不得手工创建 Agent Session。
3. 真实 Pipeline 结果必须原样传递给登记接口。测试代码不得固定或补造报告、公式、指标或有效性标志。
4. 登记响应中的 `refresh_task_id` 是后端自动创建的刷新任务标识。测试代码只允许调用 `GET /factor/performance/runs/{task_id}` 轮询，不得手工调用刷新任务创建接口。
5. 只有刷新任务状态为 `completed` 且 `completed_factors`、`incomplete_factors` 和 `summary` 的任务单元全部完整时，才能把流程标记为 `PASS_REGISTERED`。登记接口返回 201 只能证明登记阶段完成，不能代表整个入库流程完成。
6. 真实流程最终必须在 Refresh 完成后重新读取登记生成的子因子，并确认详情接口中出现刷新后的 IC/有效性数据；登记阶段的 DB 读取只能证明登记资源已落库，不能代替刷新后的最终回查。同时必须在数据库的新版 `factor_ic_summary_metrics`、`factor_ic_runs` 和 `factor_validity_status` 中核对同一复合子因子的非空计算结果及汇总关联。`factor_ic_summary_metrics` 的中位数、标准差、正值率、IS/OOS、t-stat、分层、多空和评分字段都属于可核验的计算证据，不能只检查 `mean_ic`。登记时写入的 `factor_combo_register:*` 初始有效性快照不能作为刷新证据，已废弃的 `factor_mining_symbol_window_metric` 不能作为新版指标来源。若刷新响应明确返回指标计算 `run_id`，还必须与数据库 Run 一致；有效性快照引用的每个 summary ID 还必须能在明细中找到同一因子、同一子因子标识和同一 Run。刷新失败、部分完成、查询超时或回查缺少数据分别记录为 `FAIL_REFRESH`、`FAIL_TECHNICAL` 或 `FAIL_CONTRACT`，不得转换为 `skip` 或 `xfail`。
7. 真实 Run 的启动请求必须通过同一个 Service 入口发送。首次创建要求 HTTP 202 且 `idempotent_replay=false`，同请求重放要求 HTTP 200 且 `idempotent_replay=true`；HTTP 409 只能复用响应或数据库表单中与当前表单一致的合法 `pipeline_run_id`，不能再次盲目启动。
8. 读取结构化结果遇到 HTTP 404 时，必须先查询同一 Run 的状态。状态仍未完成时继续等待并读取原 Run，状态已经失败时记录技术失败；不得把“结果暂未发布”直接当成业务无效，也不得因为一次 404 重启已完成的 Run。
9. 登记重放的 `sub_factor_id`、`registration_id` 和业务 `combo_id` 必须分别与首次响应一致，并按正整数业务语义比较；登记接口返回“已完成”409 时只查询现有表单、版本和登记映射，不能再次登记或手工创建刷新任务。
10. 真实 Run 的状态查询遇到网络异常、临时 HTTP 错误或轮询超时时，只能在原 `pipeline_run_id` 上有限重试并保留诊断；除非状态接口明确返回失败终态或 `recommended_action=retry_run`，否则不得设置 `force_fresh_pipeline_run=true`。强制新 Run 的 POST 请求不得启用底层自动重试。
11. 独立接口用例中，全新组合版本的首次登记必须是 HTTP 201 且 `idempotent_replay=false`。真实 E2E 遇到首次响应丢失时，允许把同请求自动重试得到的 HTTP 200 且 `idempotent_replay=true` 作为恢复结果，但仍须再实际重放一次并复用同一个刷新任务。两条路径都必须校验版本、子因子、因子详情、有效性快照和登记标记的 ID/哈希关系；登记因子名必须与原 Pipeline 报告一致。登记后的子因子回查必须在明确指标容器中看到 IC、有效性或计算结果，普通报告元数据不能作为刷新证据。
12. API/DB 对账以字段是否明确返回为边界：API 没有返回的字段不猜测；API 明确返回的字段包括显式 `null` 时，DB 必须存在对应列且值一致，不能因为 DB 值为 `NULL` 就跳过。JSON 布尔和 MySQL `tinyint(1)` 按业务布尔值比较，普通数值仍按 Decimal 容差比较。指标的 `summary_id`、`run_id`、范围和窗口身份必须同时满足；明确 ID/Run 命中多条 DB 记录时应判为契约失败，不能静默通过。仅旧离线替身提供聚合计数而没有 summary 明细时可保留兼容模式，真实 Repository 必须使用新版明细查询。

接口契约测试可以直接调用 `api`，数据库访问测试可以直接调用 `db`，避免 Service 层把底层问题隐藏掉。

## 8. 配置和敏感信息

- 环境地址、超时时间、重试次数等放在 `config`。
- 用户名、密码、Token 等敏感信息通过环境变量或密钥服务注入。
- 配置加载顺序建议为：默认配置 < 环境配置 < 环境变量。
- 配置中不得提交真实密码、Token 或生产数据库凭据。
- 组合因子 Agent API 地址、刷新轮询间隔、最大等待时间和轮询次数通过 `AUTOMATION_FACTOR_COMBO_*` 环境变量注入；真实测试只接受测试环境的 Factor `/api/v1` 和 Agent `/api/v2` 地址。

## 9. 报告和日志

- `reports/` 只存放运行生成的报告，不存放手工编写的说明文档。
- 报告至少支持一种机器可读格式，例如 JUnit XML。
- 日志应包含请求摘要、响应状态、耗时和失败原因，但不得打印密码和完整 Token。
- `reports/`、临时日志和截图默认加入 `.gitignore`。

## 10. 命名约定

- 测试文件：`test_*.py`
- 测试方法：`test_<业务行为>_<预期结果>`
- API 类或模块：按实际资源或领域命名，例如 `<domain>_api.py`
- Service：按实际业务领域命名，例如 `<domain>_service.py`
- Repository：按实际数据实体命名，例如 `<entity>_repository.py`
- 不使用 `common_service.py`、`万能工具.py` 等无法表达职责的名称

## 11. AI 生成实现要求

代码生成 AI 应遵守以下要求：

1. 先生成目录和最小可运行骨架，再逐步补充实现。
2. 不额外增加未在本文档中定义的复杂分层。
3. 生成配置模板和 `.env.example`，不得写入真实环境信息。
4. API、DB、Service、Case 各提供至少一个示例实现。
5. 示例测试应能通过 Mock 或测试环境配置运行，不得硬编码真实接口地址。
6. 为公共方法添加类型、参数说明和异常说明。
7. 生成 README，说明安装依赖、选择环境、运行测试和查看报告的命令。
8. 生成基础 `.gitignore`，忽略报告、日志、缓存和敏感配置。

组合因子测试数据清理只删除能按本次生成子因子唯一定位的 `factor_ic_summary_metrics`、`factor_ic_slice_metrics`、`factor_value_slice_metrics` 和 `sub_factor_refreshes` 行；`factor_ic_runs` 没有因子归属字段，可能被多个因子共享，除非后续数据模型提供明确归属，否则只保留其审计记录，不按测试流程删除。

## 12. 验收标准

- 可以通过一条命令运行全部测试。
- 可以通过参数选择测试环境。
- API 请求超时、重试和鉴权行为可配置。
- DB 查询使用参数绑定，不直接拼接用户输入。
- 至少包含一个 API 测试和一个 Service 集成测试示例。
- 测试失败时能够输出明确日志和机器可读报告。
- 测试用例中没有散落的复杂 URL、SQL 和重复业务流程。
- 各层依赖方向符合本文档定义。

## 13. 尚未确定的项目参数

以下内容需要在真正生成代码前确认，或者按本文档默认值实现：

- 实际编程语言和测试运行器
- HTTP 客户端库
- 具体数据库类型和驱动
- 报告工具，例如 Allure、pytest-html 或纯 JUnit XML
- 是否接入 CI/CD
- 是否需要 Mock、并发执行或重试失败用例
