# QuestTest

QuestTest 是一个面向 HTTP/JSON 接口自动化的 Python 3.12 pytest 框架。项目架构、职责边界和后续改造规则以 [框架设计文档](docs/automation-test-framework-design.md) 为准。

## 分层结构

```text
tests/cases -> service -> api
                     -> db
tests/cases -> tools
api、db、service -> config
```

- `tests/cases/`：真实接口、Worker 合约和真实 Agent 场景。
- `tests/unit/`：离线框架、Service 和 Repository 单元测试，不代表真实接口通过。
- `service/`：可复用的业务流程编排，不放测试断言。
- `api/`：资源或领域级 HTTP 请求封装，不放业务断言。
- `db/`：连接、事务、参数化查询和按实体组织的 Repository。
- `tools/`：无业务含义的断言、测试数据、时间和文件工具。
- `config/`：默认、测试和预发环境配置；测试环境可以保留已授权的测试凭据，生产凭据仍只能通过环境变量或密钥服务注入。
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
export AUTOMATION_API_BASE_URL='https://test-factor-backend.questvector.ai/api/v1'
export AUTOMATION_PRIVILEGED_EMAIL='replace-with-privileged-account'
export AUTOMATION_PRIVILEGED_PASSWORD='replace-with-privileged-password'
export AUTOMATION_RESTRICTED_EMAIL='replace-with-restricted-account'
export AUTOMATION_RESTRICTED_PASSWORD='replace-with-restricted-password'
export AUTOMATION_NON_OWNER_EMAIL='replace-with-non-owner-account'
export AUTOMATION_NON_OWNER_PASSWORD='replace-with-non-owner-password'
export AUTOMATION_DB_DRIVER='mysql'
export AUTOMATION_DB_HOST='127.0.0.1'
export AUTOMATION_DB_PORT='3306'
export AUTOMATION_DB_NAME='automation_test'
export AUTOMATION_DB_USERNAME='automation_user'
export AUTOMATION_DB_PASSWORD='replace-with-secret'
```

当前项目只运行测试环境，`config/test.yaml` 保留已授权的测试地址、账号和数据库配置；不要把这些配置复制到生产环境。生产密码、Token 和数据库写入凭据只能通过环境变量或密钥服务注入。DB 写操作仅允许测试环境，并且测试必须清理自身创建的数据。

运行真实用例时，框架会分别使用有权限和无权限账号调用 `POST /auth/login` 获取 JWT，再通过 `GET /me` 校验账号身份。JWT 只保存在当前 pytest 进程内；正常业务响应为 `401` 时不会自动重新登录。`AUTOMATION_API_AUTH_TOKEN` 仅保留为有权限账号的临时调试回退，不能替代无权限账号。

所有权隔离用例还需要单独的、具备正常业务权限但不是资源所有者的账号，通过 `AUTOMATION_NON_OWNER_EMAIL` 和 `AUTOMATION_NON_OWNER_PASSWORD` 提供。未配置这两个参数时，相关用例会明确标记为跳过；框架不会使用无权限账号伪造所有权隔离场景，也不会把权限不足误判为资源不存在。

## 运行

```bash
.venv/bin/python -m pytest --env test --junitxml reports/junit.xml
.venv/bin/python scripts/run_tests.py --env test
.venv/bin/python scripts/run_tests.py --env test --marker smoke
.venv/bin/python scripts/run_tests.py --env test --marker unit
```

默认命令不会访问真实接口或数据库。组合因子台用例必须显式使用 `--live`，并且基础地址必须包含 `/api/v1`：

```bash
# 表单、工作单及真实 Agent Run 接口
export AUTOMATION_FACTOR_COMBO_AGENT_UID='replace-with-test-agent-uid'
export AUTOMATION_FACTOR_COMBO_AGENT_BASE_URL='https://test-factor-frontend.questvector.ai/api/v2'
.venv/bin/python -m pytest tests/cases/factor_combo -v --live --env test

# 额外开启测试环境的 Worker 回调契约用例
export AUTOMATION_FACTOR_COMBO_WORKER_CONTRACTS='true'
.venv/bin/python -m pytest tests/cases/factor_combo -v --live --env test
```

`AUTOMATION_FACTOR_COMBO_CLEANUP_TEST_DATA=true` 会由 pytest Fixture 在用例结束时清理当前用例创建且已进入安全终态的数据，默认值为 `true` 以保持测试隔离；排查问题时可显式设为 `false` 保留数据。Fixture 传递会话到表单的归属图，Repository 会复核归属；同一会话仍有其他表单时不会删除会话。清理前如果发现生成子因子、实验或因子池被 Scope 外记录引用，或者版本身份不完整，会保守地保留整组数据，避免误删共享资源。因子池检查包括其他组合版本复用同一 `pool_id`、池成员属于其他表单或没有表单归属，以及其他表单指向该池。真实 Agent Run 启动后会暂时保护其表单；即使 Worker/直接 API 流程没有经过 Service，表单仍带有非终态或未知状态的 `pipeline_run_id` 时也继续保留，避免清理竞态。

开启清理时，框架会在事务中先核对资源归属、版本身份、实验/指标/登记映射及外部引用；任何缺失或不一致都会保留整组数据。当前表结构以具体版本主键为准，同时兼容历史组合族 ID，但必须有版本哈希和直接指针作交叉确认。确认安全后，会删除测试子因子唯一拥有的 IC summary、IC 切片、因子值切片、刷新任务和 `factor_combo_metrics`，并先清空 `metrics_id`、`experiment_id`、`best_experiment_result_id` 等指针，再按外键依赖删除组合成分、因子池成员、生成子因子、实验、组合版本、因子池、表单和无剩余表单的会话消息。`factor_ic_runs` 没有因子归属字段，可能被共享，因此只保留 Run 主记录，避免误删其他因子的计算批次。

真实端到端链路的完整通过条件是：Pipeline 返回结构化结果、登记成功、登记请求幂等重放复用同一个 `refresh_task_id`、Performance Refresh 的所有任务单元完成、登记后的子因子详情能查询到刷新数据，并且数据库的 `factor_ic_summary_metrics`/`factor_ic_runs`/`factor_validity_status` 能证明同一复合子因子的实际计算结果已经落库。summary 的中位数、标准差、正值率、IS/OOS、t-stat、分层、多空和评分等新版字段都可作为计算证据；有效性引用的 summary ID 必须能回指同一因子和 Run。API 明确返回的字段（包括 `null`）必须与 DB 一致，不能因 DB 为 `NULL` 而忽略。测试脚本不会手工调用刷新任务创建接口，也不会把登记时的占位有效性快照当成计算结果。独立接口用例严格验证首次登记 `201/false`；E2E 在首次响应丢失时可从同请求重试得到的 `200/true` 恢复，但仍会再次重放并核对相同资源与刷新任务。

真实 Run 启动和结构化结果读取有明确的恢复边界：首次启动必须返回 `202 + idempotent_replay=false`，重放必须返回 `200 + idempotent_replay=true`；启动 `409` 只允许复用当前表单已有的合法 Run。结果接口暂时返回 `404` 时会先回查 Run 状态，只有确认仍可等待才继续读取同一个 Run，避免把结果延迟误判成无效或重复启动任务。状态查询网络异常或轮询超时也不会自动创建新 Run；只有服务端明确返回失败终态或 `retry_run` 才允许使用 `force_fresh_pipeline_run=true`，且强制新建请求不会被 HTTP 客户端自动重放。

刷新轮询可通过以下环境变量调整：

```bash
export AUTOMATION_FACTOR_COMBO_REFRESH_POLL_INTERVAL_SECONDS=10
export AUTOMATION_FACTOR_COMBO_REFRESH_POLL_TIMEOUT_SECONDS=10800
export AUTOMATION_FACTOR_COMBO_MAX_REFRESH_POLLS=1080
export AUTOMATION_FACTOR_COMBO_MAX_TECHNICAL_RETRIES=2
```

组合因子台的十个主接口分别对应 `tests/cases/factor_combo/` 下十个 `test_*_api.py` 文件。Worker 回调用例使用测试环境兼容认领接口准备前置状态，不通过 Case 层 SQL 修改业务状态；接口响应与数据库持久化是两组独立断言。
