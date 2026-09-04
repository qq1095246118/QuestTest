# 因子库 4.0 测试环境 AI 执行用例

版本：`1.3`

编写日期：`2026-09-02`

用途：提供给能够调用 HTTP、MCP 和测试数据库的 AI 测试执行器。本文是执行规约，不是测试结果；执行器必须根据当次环境的真实响应和数据库记录判定结果，不能用示例数据代替。

## 0. 给执行 AI 的固定指令

```text
你是因子库 4.0 测试环境验证执行器。先执行环境硬门禁，再按本文编号顺序运行用例。
所有因子、批次、revision、publication 和 market_scope 必须动态发现，禁止硬编码示例值。
先做 R0 只读测试；只有 ALLOW_TEST_WRITES=true、测试凭据和专用清理方案同时满足时才做 R1。
不得调用反馈写入工具，不得改生产数据，不得用 DB 直接伪造指标或发布状态。
每个用例都要保存脱敏请求/响应、request_id、trace_id、业务 ID、DB 摘要和判定理由。
HTTP 200 不等于业务成功；no_recommendation、not_ready、insufficient_sample 按文档业务语义处理。
缺少必要数据时输出 BLOCKED_DATA_PRECONDITION，不要猜测预期或伪造 PASS。
需求文档定义预期契约；实际 Schema 和响应只用于核对。两者冲突时按契约判定 FAIL，并记录实际响应，不能把错误实现当成新契约。
异步批次或同步任务仍在写入时，先保存快照并将依赖终态的用例标为 BLOCKED（blocking_reason=ASYNC_STATE_MOVING）；不要跨时间点混用行数或状态作结论。
发现 P0 后停止后续有副作用的写入，只继续独立的只读诊断。
最终按 case_id 输出结构化 JSON，并汇总 FAIL、BLOCKED、未覆盖原因和清理结果。
```

## 1. 依据与优先级

执行前先读取并保存以下三份文档的版本/修改时间：

1. [因子库 4.0 功能逻辑说明](https://jjp1ynw9z1yy.jp.larksuite.com/wiki/Cd9ow9bq0iiT4Lk0uZvjlkM0ptg)
2. [因子库 4.0 技术方案](https://jjp1ynw9z1yy.jp.larksuite.com/wiki/Vr1WwD2YJi7INNkNcQjjyWcmpLg)
3. [因子库 4.0 接口+数据表文档](https://jjp1ynw9z1yy.jp.larksuite.com/wiki/X0MTwExEhiR7oFkJwTljeUKFpve)

规则优先级：

1. 8 月 31 日的“功能逻辑说明”和“接口+数据表文档”中的明确预期契约。
2. 8 月 22 日“技术方案”中的确定性约束（与当前文档冲突时，以第 1 项为准）。
3. 当前测试环境实际返回的 MCP/API Schema 和业务响应，用于验证是否符合上述契约，不能反向修改预期。
4. 技术方案中标注“待定、可能调整”的内容只能作为观察项，不能直接判定缺陷。

如果 Lark 文档无法读取，必须记录 `BLOCKED_ENV` 并停止需要文档判定的用例；不得用模型记忆或接口现状补全文档内容。

当前统一环境代码：

| 代码 | 含义 |
| --- | --- |
| `UNILATERAL_UP` | 单边上涨 |
| `CHOPPY_UP` | 震荡上涨 |
| `NARROW_RANGE` | 窄幅震荡 |
| `WIDE_RANGE` | 宽幅震荡 |
| `UNILATERAL_DOWN` | 单边下跌 |
| `CHOPPY_DOWN` | 震荡下跌 |

当前确认的路由准入规则为 `any_valid_scope`：time-series（TS）和 cross-sectional（CS）中至少一个维度满足
`metric_status=success` 且 `is_valid=true` 即具备有效性准入资格，不要求两个维度同时有效；两个维度均无效时不得进入 route。
最终是否进入 route 仍须满足当前评分版本声明的最低分及其它启用状态约束。另一个维度无效本身不能作为排除原因，
其权重处理与得分计算按对应 `score_rule_version` 和 route evidence 核验。

`fact` 用于历史评估，`forecast` 用于在线推荐。不要把技术方案里的旧代码（例如 `trend_up`、`range_up`）当作当前接口输入。

## 2. 执行边界

### 2.1 环境硬门禁

执行器在任何请求前必须确认：

- 所有 URL 的主机属于测试环境，并且不是生产域名。
- 当前环境变量 `TEST_ENV=true` 或等价的明确测试标记已设置。
- 数据库连接指向测试库；读取数据库实例名、主机和当前用户并记录脱敏摘要。
- MCP Token、JWT、HMAC 密钥只从运行时 Secret/环境变量读取，不写入用例文件、日志或报告。
- 日志中的 Token 只保留 `Bearer <redacted>`；密码、完整 JWT、完整 HMAC 签名一律不输出。
- 若要执行 R1，必须另外确认 `ALLOW_TEST_WRITES=true`、写入账号/密钥、专用 `market_scope` 和清理策略；数据库账号显示有写权限不等于已获准执行写入。

如果任一硬门禁不满足，结果为 `BLOCKED`，不能改用生产地址、猜测凭据或跳过环境校验。

### 2.2 读写分级

| 级别 | 内容 | 默认是否执行 |
| --- | --- | --- |
| `R0` | MCP 初始化、工具枚举、所有只读查询、API/DB 对账 | 是 |
| `R1` | 创建测试评估批次、启动测试评估、发布/回滚专用测试批次 | 仅 `ALLOW_TEST_WRITES=true` 且具备对应权限时 |
| `R2` | 反馈写入、修改共享环境数据、修改或删除已有 active 发布 | 默认禁止，必须在任务指令中再次明确授权 |

本规约不调用 `submit_backtest_factor_feedback`。即使工具出现在 `tools/list`，也只验证其 Schema 和 scope；反馈数据写入属于另一组需要单独授权的测试。
任何 R1 用例都不得使用现有共享 `all/default` active 批次作为写入目标；只能使用本次 `RUN_ID` 可证明归属的专用资源。

### 2.3 本轮不作为独立缺陷门禁的内容

按照此前测试约定，体验、文案、纯规范和旧兼容性问题不作为本轮功能缺陷；“孤儿记录、结束时间边界、引用不存在文档”暂不作为独立缺陷门禁。执行器仍可以记录观察值，但不能把它们与本规约中的 P0/P1 功能失败混在一起。

## 3. 运行时变量

执行器将下列逻辑变量映射到实际 Secret 或配置，不要把真实值写入报告：

```text
MCP_URL                 = 测试环境 /mcp/factor-data 地址
MCP_TOKEN               = Agent Data PAT
BACKEND_BASE_URL        = 测试环境 Backend /api/v1 地址
BACKEND_JWT             = 具备 manage_factor_library 的测试账号 JWT
INTERNAL_BASE_URL       = 测试环境内部评估服务地址（仅 R1，不能从公网猜测）
SCHEDULER_BASE_URL      = 测试环境 Scheduler 内网服务根地址（不含 /api/v1；未配置时相关用例 BLOCKED_ENV）
HMAC_ENDPOINT            = 当前文档声明的专用 HMAC endpoint
HMAC_SECRET             = 8100/内部接口测试密钥（仅 R1）
DB_DSN                  = 测试环境数据库连接（只允许测试库）
ALLOW_TEST_WRITES       = false | true
ARTIFACT_DIR            = 本次运行的独立证据目录
RUN_ID                  = 本次运行生成的 UUID
DOC_SNAPSHOT            = 本次读取的三份 Lark 文档版本/更新时间/摘要
SNAPSHOT_ID             = 一次只读对账使用的固定 DB/API 快照标识
CASE_DEADLINE            = 单个异步用例的最长等待时间
READ_ONLY_DB            = true | false（与数据库账号实际 grants 分开记录）
```

推荐先在 shell 中设置而不是在命令中粘贴密钥：

```bash
export MCP_URL='https://<test-host>/mcp/factor-data'
export MCP_TOKEN='<loaded-at-runtime>'
export BACKEND_BASE_URL='https://<test-backend>/api/v1'
export SCHEDULER_BASE_URL='http://<test-internal-host>:8120'
export ALLOW_TEST_WRITES='false'
export ARTIFACT_DIR="reports/factor4/${RUN_ID:-manual-run}"
mkdir -p "$ARTIFACT_DIR"
```

### 3.1 AI 用例最小 DSL

执行器可以把本文编号转换成 YAML/JSON manifest。每条记录必须包含以下字段；`${...}` 只表示运行时变量引用，不是要原样发送的字符串：

```yaml
case_id: REC-202
module: mcp.recommendations
priority: P1
mode: READ_ONLY
markers: [smoke, integration]
preconditions:
  - ready_forecast_exists
  - active_publication_absent
data_selector:
  market_scope: first_scope_with_ready_forecast
actions:
  - op: tools_call
    tool: environment_get_recommendations
    arguments:
      market_scope: ${market_scope}
      route_profile_key: default
      limit: 20
    save_as: recommendation
assertions:
  - kind: http_status
    actual: ${recommendation.http_status}
    expected: 200
  - kind: path_equals
    path: ${recommendation.structuredContent.data.status}
    expected: no_recommendation
  - kind: path_equals
    path: ${recommendation.structuredContent.data.reason_code}
    expected: ACTIVE_PUBLICATION_NOT_FOUND
db_checks:
  - query_name: active_publication_by_market_scope
    arguments: {market_scope: ${market_scope}, profile: default}
    expectation: {active_count: 0}
cleanup: none
on_missing_precondition: BLOCKED_DATA_PRECONDITION
stop_rule: continue_independent_readonly
evidence: sanitized_request_response_and_db_summary
```

允许的 action：`initialize`、`initialized_notification`、`tools/list`、`tools/call`、`backend_get`、`backend_post`、`hmac_request`、`db_query`、`poll`、`sleep`。允许的断言至少包括：`http_status`、`jsonrpc_result`、`path_exists`、`path_equals`、`path_type`、`enum`、`json_schema`、`sorted_desc`、`set_equal`、`db_equals`、`no_mutation`、`no_future_data`。

每个断言标记来源：`contract`（文档硬规则）、`data_dependent`（依赖当前库数据）、`oracle`（独立 fixture/手算结果）或 `exploratory`（只记录观察）。LLM 不得为缺少 oracle 的数值自行猜测预期；没有必要前置数据时将用例 `status` 设为 `BLOCKED`、`failure_class` 设为 `BLOCKED_DATA_PRECONDITION`，并在 `on_missing_precondition`/`blocking_reason` 记录缺失条件。

## 4. MCP 请求模板

### 4.1 初始化

```bash
curl --fail-with-body -sS -D "$ARTIFACT_DIR/mcp-init.headers" \
  -o "$ARTIFACT_DIR/mcp-init.body" \
  "$MCP_URL" \
  -H "Authorization: Bearer ${MCP_TOKEN}" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  --data-binary @- <<'JSON'
{
  "jsonrpc": "2.0",
  "id": "initialize-<RUN_ID>",
  "method": "initialize",
  "params": {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "clientInfo": {"name": "QuestTest-AI", "version": "1.0"}
  }
}
JSON
```

执行器必须保存响应头中的 `MCP-Session-Id`（若返回）以及服务声明的协议版本；后续请求在同一会话生命周期内复用该 header。不得把 session 值原样写入报告，只保存 hash/是否存在。

如果服务返回 `MCP-Session-Id`，后续请求还应携带 `MCP-Protocol-Version: <negotiated-version>` 和 `MCP-Session-Id: <captured-session-id>`；无状态服务或文档明确免除此 header 时记录例外。初始化响应未协商出可接受 protocol version 时，后续用例阻断。

初始化成功后发送通知（不带 `id`，不等待业务 `result`），并沿用初始化得到的 session header：

```json
{
  "jsonrpc": "2.0",
  "method": "notifications/initialized",
  "params": {}
}
```

通知请求同样使用 POST、`Content-Type: application/json` 和已捕获的 session/protocol headers；它没有 JSON-RPC `id`，客户端不得等待或重试一个业务 result。

### 4.2 工具枚举

```bash
curl --fail-with-body -sS -D "$ARTIFACT_DIR/mcp-tools.headers" \
  -o "$ARTIFACT_DIR/mcp-tools.body" \
  "$MCP_URL" \
  -H "Authorization: Bearer ${MCP_TOKEN}" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  --data-binary @- <<'JSON'
{
  "jsonrpc": "2.0",
  "id": "tools-list-<RUN_ID>",
  "method": "tools/list",
  "params": {}
}
JSON
```

### 4.3 工具调用

```bash
curl --fail-with-body -sS -D "$ARTIFACT_DIR/<case-id>.headers" \
  -o "$ARTIFACT_DIR/<case-id>.body" \
  "$MCP_URL" \
  -H "Authorization: Bearer ${MCP_TOKEN}" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  --data-binary @- <<'JSON'
{
  "jsonrpc": "2.0",
  "id": "<case-id>-<RUN_ID>",
  "method": "tools/call",
  "params": {
    "name": "<tool-name>",
    "arguments": {}
  }
}
JSON
```

响应可能是 JSON 或 SSE。执行器必须先保存原始字节，再按实际 `Content-Type` 解析；JSON 只解析完整 JSON，SSE 按事件边界解析每个 `data:` JSON，不能简单取“最后一行”或把心跳当业务结果。连接断开、空事件、多个事件和错误事件都要保留诊断；不能因为客户端默认不支持未声明的 SSE 就判服务失败。

### 4.4 Backend API 请求模板

Backend 用户接口使用具备 `manage_factor_library` 的测试账号 JWT。JWT 只从运行时环境读取：

```bash
curl --fail-with-body -sS \
  "$BACKEND_BASE_URL/<documented-recommendations-endpoint>?market_scope=${MARKET_SCOPE}&route_profile_key=${ROUTE_PROFILE_KEY}&limit=${LIMIT}" \
  -H "Authorization: Bearer ${BACKEND_JWT}" \
  -H 'Accept: application/json'
```

测试器应优先调用当前接口文档明确的 GET 端点做基线；端点路径、参数名和默认值从文档/公开 OpenAPI 动态读取，示例中的尖括号占位符不得原样发送。POST 端点只在 R1 门禁通过后调用。请求和响应文件必须脱敏后保存，不要用命令行参数直接传递密码或密钥。

### 4.5 8100/内部 HMAC 请求

HMAC 用于正式 v1 评估和内部建批次/发布。签名必须基于**实际发送的原始 body 字节**，原文格式为：

```text
v1\n<METHOD>\n<PATH>\n<TIMESTAMP>\n<NONCE>\n<SHA256(raw_body)>
```

请求头为：

```text
X-Webhook-Timestamp: <Unix seconds>
X-Webhook-Nonce: <16..128 URL-safe characters>
X-Webhook-Signature: v1=<lowercase HMAC-SHA256 hex>
```

AI 执行器必须先把 body 序列化为字节并保存，再用同一字节发送。GET 按空 body 签名。签名负向用例只使用测试密钥：

| 用例 | 变更 | 预期 |
| --- | --- | --- |
| `HMAC-001` | 正确 method/path/timestamp/nonce/body | 通过鉴权层；随后只能得到该 endpoint 文档允许的 2xx 或业务校验 4xx，不得是签名 401/403 |
| `HMAC-002` | timestamp 超过 ±300 秒 | 401/403，`retryable=false`，无写入 |
| `HMAC-003` | nonce 长度小于 16、大于 128 或重复 | 400/401/403，无写入 |
| `HMAC-004` | 错 secret、错签名、改 method/path | 401/403，无写入 |
| `HMAC-005` | 发送前重排 JSON body，但仍使用旧签名 | 401/403，无写入 |
| `HMAC-006` | 用 Backend JWT 代替 HMAC | 401/403，无写入 |

除非某个 HMAC 用例明确要求，底层 HTTP 客户端不得自动重试 POST；否则重试可能重复创建批次或启动任务。

#### HMAC 用例执行细则

以下用例只在 R1 门禁满足、存在专用测试 endpoint/market scope 且清理清单已登记后执行。`HMAC_ENDPOINT`、HTTP 方法和 body 必须从当前接口文档或服务公开配置动态取得；本节不提供可直接写入共享环境的固定 ID。

### HMAC-001 有效签名通过鉴权

- 调用：使用当前时间（服务允许窗口内）、唯一 nonce 和实际发送的原始 body，按 4.5 计算签名调用专用 endpoint。
- 断言：响应不是签名层 401/403，且 JSON/错误 envelope 符合该 endpoint 契约；若请求创建了资源，记录返回业务 ID 并加入清理清单。业务参数不合法导致的 4xx 不能误报为 HMAC 失败。
- 证据：保存 body SHA256、method/path、时间偏差、nonce hash、HTTP 状态和 request/trace ID，不保存 secret 或完整签名。
- 级别：P1（合法签名被拒绝）；写入状态不可追踪或产生重复资源时 P0。

### HMAC-002 时间戳过期/超前

- 调用：在同一专用 endpoint 上分别使用早于和晚于服务允许窗口（文档默认 ±300 秒）的 timestamp；每次使用新 nonce。
- 断言：返回 401/403 或文档规定的签名错误，结构化 `retryable=false`（若字段存在）；无 batch、task、publication 或 route 业务写入。
- 级别：P0（过期签名被接受或产生写入）。

### HMAC-003 nonce 长度与重放

- 调用：nonce 长度小于 16、大于 128、包含非 URL-safe 字符，以及先成功再原样重放同一 nonce 的请求。
- 断言：非法或重复 nonce 被拒绝；同一 raw body 不因重放创建第二资源；拒绝请求无业务副作用。若产品声明允许幂等重放，必须返回原资源且明确 `idempotent_replay=true`。
- 级别：P0（nonce 重放可重复写入）。

### HMAC-004 secret、签名和路径篡改

- 调用：分别使用错误 secret、篡改一个签名字符、改变 method、path 或 timestamp 但沿用旧签名。
- 断言：全部在鉴权层拒绝（401/403 或文档错误），不泄露签名计算细节，不产生业务写入。
- 级别：P0。

### HMAC-005 原始 body 字节绑定

- 调用：先对 body A 签名，再仅重排 JSON 键顺序、改变空白或数值表示后发送 body B，同时保留 body A 的签名；另测 UTF-8 编码差异（若 endpoint 接受）。
- 断言：签名按原始字节校验，body B 被拒绝；不能按解析后对象相等而放行。拒绝请求无业务写入。
- 级别：P0。

### HMAC-006 JWT 与 HMAC 鉴权不可混用

- 调用：使用 Backend JWT、MCP PAT、缺失 HMAC header 和 HMAC/JWT 同时存在的请求分别调用内部 endpoint。
- 断言：没有有效 HMAC 时不得进入业务层；返回 401/403 或文档错误；不因 JWT 有业务权限而绕过内部签名。
- 级别：P0。

### HMAC-007 拒绝请求审计与幂等边界

- 调用：汇总 HMAC-002 至 HMAC-006 的拒绝请求，查询审计和业务表；仅当 endpoint 文档声明幂等键/重放语义时，对合法幂等输入按文档重放一次。
- 断言：安全审计可关联 request/trace/actor，但不含 secret、完整 token 或密码；拒绝请求的业务 batch/task/publication/route 数量和状态不变；声明支持幂等时，重放只复用原业务 ID，不产生第二条记录；未声明幂等时不重复发送可能产生副作用的请求，改为记录 `NOT_APPLICABLE`。
- 级别：P0（拒绝请求改变业务状态或敏感信息泄露）。

### 4.6 调度器请求模板（GET 可为 R0，POST 仅 R1）

只有 `SCHEDULER_BASE_URL` 已由测试环境明确配置且执行环境能访问该内网地址时，才执行 Scheduler 用例；不得根据 MCP/Backend host 或端口 `8120` 猜测主机。未配置或网络不可达时记为 `BLOCKED_ENV`，不能修改 URL 绕过网络边界。health/jobs/runs 的 GET 属于 R0；手动 run 的 POST 属于 R1，并且必须使用专用 job/资源。

```text
GET  ${SCHEDULER_BASE_URL}/api/v1/scheduler/health
GET  ${SCHEDULER_BASE_URL}/api/v1/scheduler/jobs/market_environment_daily_monitor
GET  ${SCHEDULER_BASE_URL}/api/v1/scheduler/jobs/market_environment_weekly_evaluation
POST ${SCHEDULER_BASE_URL}/api/v1/scheduler/jobs/<job_key>/run
GET  ${SCHEDULER_BASE_URL}/api/v1/scheduler/runs?job_key=<job_key>&limit=50
GET  ${SCHEDULER_BASE_URL}/api/v1/scheduler/runs/<run_id>
GET  ${SCHEDULER_BASE_URL}/api/v1/scheduler/runs/<run_id>/logs?tail=200
```

手动 run 的 body 只能包含文档允许的 `force`、`actor`、`payload`；未知字段必须被拒绝。执行器不得把 Scheduler 地址暴露给公网测试客户端。

## 5. 通用判定规则

每个用例的结果只能是：

- `PASS`：所有必要断言和数据库核对均满足。
- `FAIL`：观察到与当前契约矛盾的可复现行为。
- `BLOCKED`：环境、权限、数据或依赖条件缺失，尚未测试到目标行为。
- `NOT_APPLICABLE`：当前功能开关或明确产品边界使该用例不适用。

同时填写 `failure_class`，取值为：

- `FAIL_TRANSPORT`：DNS、TLS、连接、超时或无法解析响应。
- `FAIL_AUTH`：鉴权、scope、权限或 HMAC 校验错误。
- `FAIL_CONTRACT`：JSON-RPC、Schema、字段、枚举、分页或错误 envelope 不符合契约。
- `FAIL_DATA`：API/MCP/DB、公式、数值、时间或关联关系不一致。
- `FAIL_BUSINESS`：服务错误地接受/拒绝业务状态，例如失败批次被发布。
- `FAIL_TECHNICAL`：后台任务、事务、计算或服务内部错误。
- `BLOCKED_ENV`：测试地址、凭据、网络或写入开关缺失。
- `BLOCKED_DATA_PRECONDITION`：计划要求的真实 revision、publication、metric 或 fixture 不存在。
- `BLOCKED_TIMEOUT`：在限定 deadline 内没有到达可判定状态。
- `BLOCKED_DOC`：无法读取本轮要求的契约文档或 Schema，无法可靠建立预期。
- `ASYNC_STATE_MOVING`：依赖对象在用例期间仍被后台任务修改，快照无法稳定。

`HTTP 200 + status=no_recommendation` 不是自动失败，必须结合 `reason_code` 判断。`insufficient_sample` 是业务状态，不是服务故障；`partial_fail`、`failed` 才是评估故障。

状态与严重级别约束：`severity` 只对 `FAIL` 必填；`PASS`、`BLOCKED`、`NOT_APPLICABLE` 的 severity 为 `null`。`failure_class` 在 `PASS`/`NOT_APPLICABLE` 时为 `null`，在 `BLOCKED` 时必须以 `BLOCKED_` 开头或使用 `ASYNC_STATE_MOVING`；`reproducible` 仅在已实际执行并得到结论的 `PASS`/`FAIL` 填布尔值，其余填 `null`。

每个失败必须记录：

- `case_id`、严重级别、首次发现时间和复现次数。
- 脱敏后的请求摘要、HTTP 状态、JSON-RPC id。
- `meta.request_id`、`meta.trace_id`（若有）。
- 原始响应文件路径和解析后的最小 JSON 摘要。
- 相关 `batch_uid`、`task_id`、`factor_ref`（这些业务 ID 可保留）。
- DB 查询时间、SQL 模板名、参数脱敏摘要和结果摘要；不要在报告中输出完整原始 payload 中的密钥或个人信息。
- 预期、实际、影响范围和是否可独立复现。

## 6. 动态测试数据发现

不要硬编码本轮已有的某个因子或批次作为唯一前置。执行器按以下顺序动态发现：

1. 调用 `factor_catalog_stats` 获取当前候选数量。
2. 调用 `factor_search` 或 `factor_rank` 取不超过 10 个候选。
3. 通过 `factor_get_detail` 和（必要时）`factor_get_validity` 确认 `active + valid`。
4. 优先选择已有完整环境指标的因子；若没有，记录为正向链路 `BLOCKED`，不要伪造指标。
5. 从 `environment_get_daily` 取最新 `fact`、`forecast`，从 Backend/DB 发现已有批次和 active publication。
6. 需要 R1 写入时，生成唯一 `RUN_ID` 和唯一幂等输入，并把所有新资源登记到清理清单。

动态发现规则：工具名、端点路径、参数名、枚举、limit 上下限、可选 profile 和响应必填字段必须优先从本轮 Lark 文档、MCP `tools/list` 或公开 OpenAPI 读取。文档未声明的 profile（例如 `balanced`、`time_series`、`cross_sectional`）不得强行调用；调用后返回空结果不能证明 profile 合法。示例值 `all`、`default`、日期和因子 ID 只能作为说明，不能作为执行器固定输入。

正向/边界场景需要独立数据时，执行器只能使用以下顺序：

1. 从现有测试库动态选择满足条件的真实记录，并保存选择条件和快照时间。
2. 使用产品提供的测试 fixture/API 创建带 `RUN_ID` 标记的资源（仅 R1）。
3. 若两者都不可用，输出 `status=BLOCKED`、`failure_class=BLOCKED_DATA_PRECONDITION`；不得直接改生产记录、手工插入指标或伪造 API 响应。

每个 fixture/oracle 必须登记 `fixture_id`、输入版本、创建者、有效期、清理动作和预期来源（`contract`/`data_dependent`/`oracle`/`exploratory`）。没有独立 oracle 的数值只做一致性检查，不自行推导“正确值”。

### 6.1 本轮回归优先项（仅作观察线索）

以下是 2026-09-02 对测试环境的已知观察，不是新的预期契约。执行器恢复连接后应优先用对应编号复核；复核必须重新取快照，不能直接复用这些数字：

| 观察线索 | 优先复核用例 | 当前证据边界 |
| --- | --- | --- |
| MCP 请求曾返回 `503 DEPENDENCY_UNAVAILABLE/reader_db` | MCP-001~005 | 先确认依赖恢复；503 是环境/技术阻断，不要把所有工具都报成独立缺陷 |
| Backend 日期参数对已有 daily 记录返回空集合，或未知日期参数被忽略 | ENV-103、MCP-006 | 只在文档明确支持日期过滤时判契约失败；`as_of` 结果单独核对 |
| `limit=0`、负数、超上限或非数字被静默归一 | ENV-109、MCP-006 | 按各工具 Schema 的实际边界判定；不要跨工具套用 100/1000/200 |
| evaluator 仍 `running` 但 publication/active route 已出现且持续变化 | LIFE-409/410、REC-204/209、DB-604 | 先记录变化时间序列；若完整原子发布是契约，则合并为一组发布/准入缺陷 |
| route 的 TS/CS validity 或 score 与指标不一致 | MET-309、CALC-506、DB-605 | 必须按同一 metric 外键、版本和快照重算；不能仅凭一个 API 数字判定 |
| 同 batch 重发布时 publication 身份/历史 route 变化 | LIFE-412、REC-213、DB-604 | 只有文档要求幂等且保留历史时才判失败；先排除异步重建竞态 |
| route.environment_date 与 snapshot/missing_dates 的关系不明确 | CALC-513 | 字段语义未被文档确认前只作观察，不单独定性 |

因子引用必须符合 `factor:<正整数>` 或 `sub_factor:<正整数>`。母因子和子因子应分别覆盖一次；母因子测试前要记录当前子因子关系快照。

## 7. 用例总览

| 模块 | 用例范围 | 默认级别 | 目标 |
| --- | --- | --- | --- |
| M0 门禁与协议 | `MCP-001`~`MCP-019` | R0 | 确认连的是正确测试服务，协议、鉴权、Schema、会话、分页和错误 envelope 正常 |
| H0 内部签名 | `HMAC-001`~`HMAC-007` | R1 | 在专用测试资源上验证 HMAC 鉴权、重放保护和拒绝请求无副作用 |
| E1 环境数据 | `ENV-101`~`ENV-112` | R0 | 验证 fact/forecast、revision、as_of、时间可见性和 DB 一致性 |
| 推荐路由 | `REC-201`~`REC-213` | R0/条件 R1 | 只读验证预测路由、无推荐原因、排序和一致性；边界 fixture/发布切换需 R1 |
| F1 指标/标签 | `MET-301`~`MET-311` | R0 | 验证时序/截面指标、批次隔离、准入条件、标签证据 |
| L1 批次生命周期 | `LIFE-400`~`LIFE-418` | R0/R1 | 先确认发布模式契约，再验证建批次、快照、幂等、评估、发布、回滚和 Scheduler |
| C1 计算正确性 | `CALC-501`~`CALC-513` | R0/R1 | 核查样本切分、无未来泄漏、公式、得分、引用和数值精度 |
| D1 数据与安全 | `DB-601`~`DB-613` | R0/R1 | 核查表关系、审计、权限、事务原子性、状态不变量和历史保留 |

按当前编号共 107 条用例（其中 R0 只读/协议用例、R1 专用写入用例和条件阻断用例必须分别统计，不能把 BLOCKED 当作 PASS）。

## 8. M0：门禁与 MCP 协议

### MCP-001 初始化握手

- 目的：确认服务支持声明的 MCP 协议，并返回服务身份。
- 前置：`MCP_URL`、`MCP_TOKEN` 已通过门禁。
- 调用：4.1 初始化模板；收到成功响应后发送一次 `notifications/initialized`（无 `id`，无 `result` 要求）。
- 断言：HTTP 成功；JSON-RPC `result` 存在；`protocolVersion` 可接受；`serverInfo.name`、`serverInfo.version` 存在；没有 JSON-RPC `error`；服务接受 initialized 通知后可继续处理 `tools/list`。
- 失败级别：P0（无法初始化时后续 MCP 用例全部 `BLOCKED`，不要重复报成多个缺陷）。
- 证据：保存 headers/body，记录响应耗时，不保存 Authorization 原值。

### MCP-002 工具清单与 4.0 Schema

- 目的：确认部署包含当前契约要求的工具，并且每个工具的参数 Schema 与文档一致；不因服务额外暴露工具而失败。
- 调用：4.2 `tools/list`；若结果提供 `nextCursor`/等价游标，按原 session、原参数逐页读取，设置最大页数并检测重复游标，不能无限跟随或把游标当工具参数。
- 断言：按当前文档快照动态读取 required/optional 工具集合；required 工具必须存在且名称、description、`inputSchema` 可解析。逐个用 JSON Schema 校验 `required`、字段类型、枚举、`minimum/maximum` 和 `additionalProperties`（只有 Schema 明确为 `false` 时才拒绝未知字段）。环境代码、`label_kind`、`evaluation_type` 和每个工具的 limit 上下限均从该工具 Schema/文档读取，不跨工具套用固定上限；额外工具只记录名称、权限和是否写入，不把数量写死。
- 观察：反馈工具是否出现只记录，不调用写入；工具清单是否包含版本/分页提示。
- 失败级别：P1（required 工具缺失或 Schema 与契约冲突）；额外工具/文案差异不单独报错。

### MCP-003 鉴权边界

- 目的：确认无 Token、错误 Token、错误 scope 不能读取业务数据。
- 调用：同一 `tools/list` 或只读 `tools/call` 分别删除 Authorization、使用随机无效 Token、使用已知无读 scope Token（若测试环境提供）。
- 断言：返回 401/403 或 MCP `isError=true`；不返回有效业务 `data`；错误包含可定位 code/request id；不泄露 Token。
- 注意：不要用真实生产 Token 做负向测试。
- 失败级别：P0（未鉴权可读）/P1（错误码或泄露问题）。

### MCP-004 JSON-RPC 与未知输入

- 目的：确认协议错误不会触发业务动作。
- 调用：缺失 `jsonrpc`、未知 method、未知 tool name、arguments 多余字段、arguments 类型错误各一次。
- 断言：返回规范 JSON-RPC/MCP 错误；`isError=true` 或顶层 `error`；不产生 DB 写入；错误可区分参数错误和工具不存在。
- 失败级别：P1；仅文案差异不单独报错。

### MCP-005 成功 envelope 和关联 ID

- 目的：确认成功结果可审计，且不把某一个工具的可选字段误当成所有工具的必填字段。
- 调用：从 MCP-002 选择一个不写入且有可用前置的 required 工具；保存原始响应。
- 断言：响应符合该工具 Schema 和 MCP envelope；契约明确要求时必须有 `structuredContent.data`、`meta.request_id`、`meta.trace_id`、`schema_version`、`data_as_of`、`source_versions`、`next_cursor`、`truncated`、`warnings` 或 `quota`。未在该工具 Schema/文档声明的字段不得强制要求，也不得凭缺失字段判错。若返回 request/trace ID，格式可追踪并能与响应或服务日志关联；JSON-RPC `id` 必须原样对应请求。
- 失败级别：P1（声明的必填 envelope/关联 ID 缺失或类型错误）。

### MCP-006 限制值和输入校验

- 目的：验证边界值不会静默扩大查询或触发异常。
- 调用：对 MCP-002 中每个带 `limit` 的 required 工具，动态读取其 `minimum`、`maximum` 和 default。始终测试已声明的 minimum/default；只有 Schema 声明 maximum 时才测试 maximum 和 maximum+1。没有 maximum 时，用当前数据量可承受的有界值探测并记录“上限未声明”，不得自行制造上限。另测 0、负数、浮点和字符串；对日期发送非法日期、带时区和不带时区的 `as_of`；再发一个明确未知字段。请求体示例必须保留在证据中（密钥脱敏）。
- 断言：合法边界先通过参数校验；若响应因文档声明的响应字节/行数保护返回 `RESPONSE_TOO_LARGE` 等结构化业务错误，按容量保护契约单独判定，不能误报为参数边界失败。成功结果返回条数不超过请求 limit；非法值返回结构化参数错误（或文档明确的 4xx），不得把 0、负数、浮点或字符串静默改成默认值/上限；未知字段仅在 Schema `additionalProperties=false` 时必须拒绝，否则记录服务实际兼容行为；不产生业务写入。若 Backend 与 MCP 规则不同，分别按各自契约判定。
- 失败级别：P1（违反明确契约）；仅兼容性差异按观察记录。

### MCP-007 可选反馈能力的安全发现

- 目的：确认反馈工具的暴露与 scope 受功能开关控制。
- 调用：只读取 `tools/list` 和工具 schema。
- 断言：若 `AGENT_DATA_STRATEGY_FEEDBACK_ENABLED=false`，写入工具不应出现；若出现，必须验证调用凭据是否有 `strategy.feedback.write` 的明确授权并记录为安全观察。普通浏览 PAT 不应可写。
- 禁止：本用例不得调用 `submit_backtest_factor_feedback`。
- 失败级别：P0（无授权可写）/NOT_APPLICABLE（功能关闭且符合预期）。

### MCP-008 响应内容协商

- 目的：确认调用方按服务声明的媒体类型解析响应，不把未声明的 SSE 兼容性当成业务故障。
- 调用：先读取初始化结果、工具说明和当前接口文档中的 `Content-Type`/Accept 约定；对声明支持的每一种媒体类型调用同一只读工具。至少覆盖 `application/json`；只有服务或文档明确声明时才覆盖 `text/event-stream` 和组合 Accept。
- 断言：声明支持的媒体类型返回可解析业务内容，业务字段、request id 和 warning 语义一致；SSE 支持时逐事件解析 `data:`，忽略空行/注释/心跳，并校验事件内 JSON-RPC id。未声明的媒体类型返回文档规定的 406/415 属于 `NOT_APPLICABLE` 或兼容性观察，不单独报告功能缺陷；若服务声称支持却返回不可解析内容，判 `FAIL_CONTRACT`。
- 失败级别：P1（仅限违反明确媒体类型契约）。

### MCP-009 只读调用无副作用

- 目的：确认环境和查询工具不调用上游、不触发 LLM、不写业务表。
- 调用：从 MCP-002 动态选择所有声明为 read-only 且有可用前置的 required 工具（至少覆盖环境、推荐、指标、标签能力，如当前契约提供）；每个工具连续调用一次。调用前后查询相关表的行数、`updated_at` 和任务表。
- 断言：除审计/访问日志（若产品明确记录）外，`market_environment_daily`、批次、指标、路由和反馈表没有新增或修改。若后台 evaluator 正在运行，必须用同一 `SNAPSHOT_ID` 比较请求前后记录，排除并发写入；无法排除时标记 `BLOCKED_DATA_PRECONDITION/ASYNC_STATE_MOVING`，不能把全库行数变化归因于只读调用。仅凭客户端响应不能断言“未触发 LLM/上游”，这类结论须有服务端 trace/任务审计证据。
- 失败级别：P0（只读工具修改业务数据）/P1（隐式触发昂贵计算）。

### MCP-010 会话复用与重连

- 目的：验证 MCP 会话状态不会因每次 HTTP 请求被意外丢失。
- 前置：初始化响应若返回 `MCP-Session-Id` 或等价会话头，必须保存其脱敏值并在 `notifications/initialized`、`tools/list`、`tools/call` 中复用；若文档声明无状态 HTTP，则记录该声明。
- 调用：同一会话完成初始化、通知、工具调用；随后断开并用新连接重连，重复初始化并调用同一工具。
- 断言：有状态服务拒绝缺失/错误 session，或按文档返回可定位错误；同一 session 的工具调用可用，重连后不会串用旧状态；无状态服务两次结果符合契约。不得把 session ID 或 Token 写入报告。
- 失败级别：P1（违反明确会话契约）；未声明 session 时仅记录观察。

### MCP-011 malformed JSON、重复 ID 与未知版本

- 目的：确认协议解析错误不会进入业务层或留下副作用。
- 调用：分别发送截断 JSON、顶层数组、缺失/错误 `jsonrpc`、重复 `id`、未知 protocol version、`tools/call` 缺失 `params` 和错误的 `arguments` 类型；每次使用独立请求 ID 并保存原始字节。
- 断言：返回 JSON-RPC/MCP 规定的 parse/invalid-request/invalid-params 错误，或文档规定的 4xx；不返回有效业务 data，不创建任务/批次，不修改业务表。重复 ID 的行为按协议/文档判定，不能把客户端超时当作服务成功。
- 失败级别：P1；若畸形请求触发写入则 P0。

### MCP-012 超时、取消与有限重试

- 目的：验证慢响应、客户端取消和网络重试不会重复业务动作。
- 前置：仅使用文档提供的可控慢只读工具/fixture，或 R1 专用且明确幂等的测试端点；不得通过猜测超短 timeout 把正常请求制造成假失败，不得对共享写入端点施加重试。没有可控慢端点或取消协议时相应分支 `BLOCKED_ENV`/`NOT_APPLICABLE`。
- 调用：设置文档允许的短客户端 timeout；在响应前关闭连接/发送取消（若 MCP/HTTP 契约支持）；对可重试网络错误重复一次，并记录 attempt。若服务统一返回依赖 503，先形成一个根因阻断并断路，不对每个工具重复超时/重试。
- 断言：超时有明确 `FAIL_TRANSPORT`/可重试语义；服务端不会因客户端重试生成重复任务；底层客户端只自动重试文档允许的幂等方法。若无服务端取消能力，记录“取消未声明”，不猜测已取消。
- 失败级别：P1（重复副作用或错误重试策略）。

### MCP-013 限流、配额与退避

- 目的：确认超过速率/配额时返回可识别的业务错误而非部分或伪造数据。
- 前置：服务文档或 tools/list 声明限流/配额；没有声明时只做低并发观察，不主动压垮测试服务。
- 调用：在文档允许的低并发范围内逐步增加请求，记录 429、`Retry-After`、`meta.quota` 和 request id；收到 429 后按服务建议退避一次。
- 断言：限流响应不含有效业务结果，错误可定位且不改变 DB；`Retry-After` 可解析时遵守；退避后请求不会把旧响应当新响应。未声明限流策略时标记 `NOT_APPLICABLE`。
- 失败级别：P1（限流绕过、配额泄漏或错误数据）。

### MCP-014 大响应、截断与异常 SSE

- 目的：验证大结果的分页/截断不会静默丢失或拼接错误。
- 调用：动态选择结果量最大的只读工具；Schema 声明 maximum 时使用该 maximum，未声明时沿用 MCP-006 的有界探针并记录选择依据，不能自行假设“最大值”。若支持 SSE，注入/接收多事件、空 data、注释行和断开事件（只在客户端解析器测试中模拟，不改服务；不把离线解析器模拟结果当服务端通过）。
- 断言：`truncated`、`next_cursor` 与实际数据一致；分页无重复/遗漏；malformed SSE 被报告为解析失败，不产生半成品业务结果；客户端有明确最大响应/超时保护。
- 失败级别：P1。

### MCP-015 并发只读一致性

- 目的：确认并发查询不会混合不同 publication、batch 或 as_of 快照。
- 前置：固定 `SNAPSHOT_ID`；若底层数据在变动，先标记 `ASYNC_STATE_MOVING`。
- 调用：并发调用同一工具 2~8 次，参数完全相同，再串行调用一次。
- 断言：同一快照下业务主键集合、status/reason、batch/publication 和排序一致；差异只能是 request/trace ID；若快照在执行中变化，响应必须带明确 data_as_of/version，否则标记 `BLOCKED`。
- 失败级别：P1（出现跨批次串线或不可解释差异）。

### MCP-016 已发现工具能力矩阵

- 目的：避免只验证少数核心工具，却把其余已暴露能力当作未覆盖。
- 调用：对 `tools/list` 返回的每个工具生成一行能力矩阵，字段至少包括 `name`、read/write、所需 scope、Schema hash、必填参数、枚举、limit、分页、媒体类型和对应 case_id。对每个声明为 read-only 的工具，动态选择一组合法参数执行一次；对每个声明为 write 的工具只执行 Schema/权限静态检查，不调用。
- 断言：每个工具都有明确的 `covered`、`not_applicable` 或 `blocked` 状态和理由；required 工具缺失、合法参数和前置齐全却无法按 Schema 调用、或 write 工具无权限边界说明时判契约失败。复杂 read-only 工具若无法动态生成完整 scope/参数，必须在该工具矩阵行标记 `status=BLOCKED`、`failure_class=BLOCKED_DATA_PRECONDITION` 并列出缺失条件，不能猜参数，也不能因此把其它工具或整个 MCP-016 判 FAIL。
- 失败级别：P1（未覆盖的 required/read-only 能力或越权暴露）；仅额外工具不要求业务断言。

能力矩阵至少按以下业务族归类（实际名称以 `tools/list` 和文档为准）：因子目录/搜索/详情/排名、环境 daily/推荐、环境指标/切片/有效性、公式/指标范围、交易标的 universe、Schema/元数据以及反馈/写入。对目录类工具还要记录 active/valid 与 point-in-time warning；对排名类工具核对 symbol 非空、币种/市场分区和稳定排序；对切片类工具核对窗口、cursor 绑定和总量；对 universe 类工具核对去重、停牌/无行情语义；对 Schema 类工具核对版本和未知字段。某一业务族没有工具时，若文档要求该能力则 `BLOCKED_DOC`/`FAIL_CONTRACT`，若明确不在本服务边界则 `NOT_APPLICABLE`。

### MCP-017 `content` 与 `structuredContent` 一致性

- 目的：确认 MCP 客户端可从标准内容块读取与结构化结果相同的业务事实。
- 调用：选取返回 `content[]` 和 `structuredContent` 的成功工具，解析文本 JSON（若声明为 JSON）和结构化对象；错误响应也做一次映射检查。
- 断言：两种表示中的业务主键、status/reason、计数和版本一致；文本仅为人类说明时按 Schema 标记，不强行 JSON 解析；`isError=true`、顶层 JSON-RPC `error` 和 HTTP 错误的映射符合文档，不能出现一边成功一边失败或把错误文本当数据。
- 失败级别：P1。

### MCP-018 分页游标完整性

- 目的：验证游标绑定查询条件和会话，防止篡改、过期或跨工具复用造成数据串线。
- 前置：工具 Schema 声明 `next_cursor`/cursor 分页。
- 调用：先用小 limit 获取 cursor，再原样续页；分别篡改一个字符、改 limit、改 filter、换工具或换 session 后重放 cursor；测试过期 cursor（仅按文档允许的方式）。
- 断言：原条件续页无重复/遗漏；不匹配、篡改或过期 cursor 返回结构化错误或空结果（按契约），不会返回另一查询的数据；未声明 cursor 的工具标记 `NOT_APPLICABLE`。
- 失败级别：P0（跨租户/跨查询泄露）/P1。

### MCP-019 point-in-time 状态与 warning 语义

- 目的：区分“当前有效”与“某一查询时点有效”，避免把 catalog/route 的全局 valid 当成时点有效。
- 调用：对支持 `as_of`/`data_as_of` 的工具分别查询当前、历史和未来时点；记录 `warnings`、validity/status 和 publication/batch 时间。
- 断言：返回的对象在请求时点可见；从契约建立 `expected_warnings` 清单，预期 warning（例如契约明确的 `CURRENT_LIBRARY_STATUS_NOT_POINT_IN_TIME`）按要求出现且被完整保留时是 PASS 证据，不是缺陷；预期 warning 缺失/被客户端丢弃才判失败，未知 warning 先记录观察。任何 warning 都不能被忽略后把 `library_status=valid` 自动等同于 point-in-time valid；未来数据不得泄漏。工具不支持时点参数时标记 `NOT_APPLICABLE`。
- 失败级别：P0（未来/跨时点数据泄漏）/P1（warning 被静默丢弃）。

## 9. E1：环境数据

### ENV-101 fact 当前修订查询

- 目的：验证默认查询只返回当前可见的事实修订。
- 调用：`environment_get_daily({"label_kind":"fact","limit":100})`。
- 断言：items 按文档约定排序；每条 `label_kind=fact`；`is_current=true` 的语义与 DB 一致；日期、label_status、revision、available_at、raw payload 摘要存在；`returned_count` 等于实际 items 数。
- DB：按返回日期和 revision 查询 `market_environment_daily`，核对 `label_code`、`label_status`、`revision`、`is_current`、`available_at` 和 payload hash。
- 失败级别：P1；出现未来不可见记录为 P0。

### ENV-102 forecast 当前修订查询

- 目的：验证预测记录具备在线推荐所需的时间和概率信息。
- 调用：`environment_get_daily({"label_kind":"forecast","limit":100})`。
- 断言：记录为 `forecast`；`label_status=ready` 的记录其 `label_code` 属于六枚举，`available_at` 可解析为 timezone-aware 时间，概率/置信度/model/schema 字段按当前工具 Schema 的 required/nullable 规则校验。`not_ready` 或 `invalid` 记录允许业务字段为空，但必须有明确状态和错误/缺失证据；不能把“字段为空”自动判成失败或 ready。同日期当前修订唯一。
- DB：核对 `market_environment_daily` 与上游标准化字段和 raw payload。
- 注意：文档中的旧 `observed` 不作为当前接口值。
- 失败级别：P1；使用尚未 available 的预测为 P0。

### ENV-103 日期过滤

- 目的：验证日期过滤不会返回邻近日期或混合 fact/forecast。
- 前置：从无日期查询或 DB 动态选择一条当前记录的 `environment_date`，另选一个不存在日期；不得写死示例日期。
- 调用：分别以文档规定的日期参数名请求 fact、forecast 和不存在日期；另测非法日期格式、未知日期参数别名（例如 `date`/`environmentDate`，仅当接口允许发送未知字段）。
- 断言：命中时只返回精确日期和指定 `label_kind`；不存在日期返回空集合且 envelope 正常；合法日期不能因参数名/格式被静默忽略后返回默认最近记录；非法格式返回文档规定的 4xx；不因空结果返回 500。若文档明确日期参数不受支持，则该分支 `NOT_APPLICABLE`，不能以现状推断支持。
- DB：将请求日期与 `market_environment_daily.environment_date` 精确对账。
- 失败级别：P1。

### ENV-104 `as_of` 修订可见性

- 目的：验证历史回放按 `available_at` 选择当时可见的最新 revision。
- 前置：DB 中存在同日期多 revision，或从环境表发现可构造的两个 available_at 时间点。
- 调用：不传 `as_of`、传早于新 revision available_at 的时间、传晚于新 revision 的时间。
- 断言：早时间看不到尚未 available 的 revision；晚时间选择最新可见 revision；历史 revision 不被删除或覆盖；返回的 revision 与 DB 可见性查询一致。
- 若当前无多 revision：记录 `BLOCKED`，不把无数据误判成通过。
- 失败级别：P0（未来 revision 泄漏）/P1（选择错误 revision）。

### ENV-105 current 指针唯一性

- 目的：验证同日期同类型只能有一个 current。
- DB：查询同一 `environment_date + label_kind` 的所有 revision，检查 `is_current=1` 计数。
- 断言：计数为 0 或 1（无 current 必须与 `not_ready`/无数据语义一致），不能大于 1；旧 revision 仍保留。
- 失败级别：P1。

### ENV-106 事实/预测字段完整性

- 目的：验证标准化字段不会把无效输入伪装成 ready。
- DB/API：逐条检查 ready fact/forecast 的必需字段和 timezone；invalid/not_ready 记录允许缺少业务值，但必须有明确状态。
- 断言：未知 label、非法日期、schema 不匹配不会进入 ready 正式评估；`raw_payload` 与标准化结果可追溯。
- 失败级别：P0（invalid 记录进入推荐）/P1。

### ENV-107 available_at 防未来信息

- 目的：确认任何查询时点都不会看到未来不可见的环境。
- 调用：使用当前时间、未来时间、历史 `as_of` 各查询；对每条记录验证 `available_at <= query_as_of`（未传时使用服务当前 data_as_of）。
- 断言：不满足可见条件的记录不返回；推荐同样遵守该条件。
- 失败级别：P0。

### ENV-108 API 与 MCP 环境对账

- 目的：确认 Backend API、MCP 和 DB 使用同一标准化数据。
- 调用：Backend `GET /market-environments/daily` 与 MCP 同条件查询。
- 断言：同一日期/kind/revision 的业务字段一致；差异只能是明确标注的 envelope/分页字段；不能出现一方有记录另一方无记录而没有业务原因。
- 失败级别：P1。

### ENV-109 分页/截断语义

- 目的：确认 limit、next_cursor、truncated 组合不会丢数据或重复数据。
- 调用：用最小 limit 分页（仅在返回 `next_cursor` 时继续），将各页按主键和日期去重后与大 limit 结果比较。
- 断言：无重复、无遗漏；不自动无限跟随 cursor；`truncated` 与 cursor 存在性一致。
- 失败级别：P1。

### ENV-110 预测窗口字段

- 目的：核对 forecast 有效窗口和预测日期关系。
- DB/API：检查 `effective_from/effective_to`（若当前代码明确返回）、`forecast_date`、`available_at` 和 horizon。
- 断言：仅对 Schema/文档声明为 required 且非 nullable 的窗口字段强制检查；返回的时间均有时区，窗口不反向；在线推荐的查询时点落在有效窗口或按当前契约给出明确 no recommendation 原因。字段显式为 `null` 时，必须按该工具的 nullable 规则与 DB 对账，不能自行当作 0/空串。
- 若当前实现把窗口字段置空且文档未声明可为空：记录 P1；若功能边界明确不返回或允许为空则 `NOT_APPLICABLE`/观察，不把数据自然缺失当作协议故障。
- 失败级别：P1（仅限违反已确认的 required、nullable 或窗口方向契约）。

### ENV-111 环境特征与 raw payload 对账

- 目的：确认 features/probabilities 等结构化字段没有被错误改写。
- DB/API：对返回记录计算标准化字段与 raw payload 对应字段的逐字段差异。
- 断言：映射字段值、单位、空值语义一致；原始 payload 可回放；payload hash 稳定。
- 失败级别：P1。

### ENV-112 同步幂等观察

- 目的：验证重复同步不会产生重复 current 或重复业务记录。
- 前置：只能在有明确测试同步入口或 Scheduler 测试作业且 `ALLOW_TEST_WRITES=true` 时执行；否则 `BLOCKED`。
- 调用：对同一上游事件执行一次和重复执行一次。
- 断言：`source_event_id + payload_hash` 幂等；相同输入不新增重复正式记录；内容冲突被拒绝并留下审计。
- 失败级别：P1。

## 10. R0：推荐路由（只读）

### REC-201 无 forecast

- 目的：验证预测缺失时不回退 fact。
- 前置：选择没有 ready forecast 的历史 `as_of` 或测试市场范围。
- 调用：`environment_get_recommendations({"market_scope":"<scope>","as_of":"<time>","limit":20})`。
- 断言：HTTP/MCP 业务请求成功；`data.status=no_recommendation`；`reason_code=ACTIVE_FORECAST_NOT_FOUND`；`items=[]`；不能把 fact label 当 forecast。
- 失败级别：P0（事实兜底）/P1（错误 reason）。

### REC-202 有 forecast 无 publication

- 目的：验证当前测试环境常见的“有预测、无发布”状态。
- 调用：选择最新 ready forecast 的市场范围。
- 断言：`status=no_recommendation`、`reason_code=ACTIVE_PUBLICATION_NOT_FOUND`；forecast 信息仍可解释；`items=[]`；不是 HTTP 500。
- 失败级别：P1。

### REC-203 无 eligible factor

- 目的：区分“有发布但当前环境没有合格因子”和“没有发布”。
- 前置：需要已有 active publication 且目标 label 没有合格 route；否则 `BLOCKED`。
- 断言：`reason_code=NO_ELIGIBLE_FACTOR`；不能错误报告为 publication missing。
- 失败级别：P1。

### REC-204 正向推荐结构

- 目的：验证有 active publication 时只返回当前 forecast 对应环境的推荐。
- 前置：动态发现一个**已完成且矩阵完整**的 active publication，并确认其 market scope/profile、forecast 可见性和 route 快照均稳定；只有部分环境、running batch 或正在变化的 publication 不能作为正向 PASS 前置。
- 调用：`environment_get_recommendations` 使用发现的 scope/profile/as_of；不得把 `all`、`btc` 或当前日期当固定输入。
- 断言：publication 属于一个完成 batch；items 的 `label_code` 等于当前可见 forecast label；每条 factor 满足 active 状态、TS/CS 至少一个维度 `success + is_valid=true`、当前 score 门槛及其它准入约束；不因另一个维度无效而错误排除；items 按 Schema/文档规定的稳定排序返回。若 active publication 不完整，记录关联的生命周期缺陷并将本用例标为 `BLOCKED`，不能把空结果当正向通过。
- 失败级别：P0（返回失效或错误环境因子）/P1。

### REC-205 market_scope 隔离

- 目的：防止不同市场范围串数据。
- 调用：对两个存在的 market_scope 查询相同 forecast 日期。
- 断言：publication、routes、rank 和 factor 集合均绑定各自 market_scope；不存在范围返回明确空/业务错误，不借用 `all`。
- 失败级别：P1。

### REC-206 route profile 权重

- 目的：验证 profile 选择实际影响 routing score，而不是始终返回 default。
- 前置：同一 publication 有至少两个因子且 TS/CS 分数不同；从文档、批次 `evaluation_config` 或公开 Schema 动态读取实际可用 profile 和权重。未声明的 profile 不调用。
- 调用：对每个声明支持的 profile 查询同一 scope/as_of；保存请求中的 profile 与响应中的 profile。
- 断言：按配置中的权重和归一化公式用 Decimal 重算 `routing_score`，允许文档规定的容差；profile 不被静默改写；若只有一个 profile，记录 `BLOCKED_DATA_PRECONDITION`，不能假设存在 `balanced/time_series/cross_sectional` 或固定 0.5/0.8 权重。
- 失败级别：P1。

### REC-207 评分门槛边界

- 目的：验证当前评分规则声明的最低分边界和有效性条件。
- 模式：现有真实记录恰好覆盖边界时为 R0；创建专用边界 fixture 时为 R1，并须通过测试 API/fixture 创建和清理，不得直接伪造 route/metric。
- 前置：从文档、`score_rule_version`/`evaluation_config` 或公开 Schema 动态读取 threshold、比较运算符和精度；从 DB 选取或通过专用 fixture 准备 threshold 前后可区分的 route 分数。若没有边界 fixture，标记 `BLOCKED_DATA_PRECONDITION`。
- 断言：低于 threshold 按规则不进入推荐；等于 threshold 按规则的 `>=`/`>` 语义处理；高于 threshold 按规则处理；TS/CS 至少一个维度必须 `success + is_valid=true`，另一个维度无效不阻止准入，两个维度均无效时即使分数字段异常偏高也不得进入。不得把 60 或三位小数当成所有版本固定规则。
- 失败级别：P1。

### REC-208 排名稳定性和并列

- 目的：确认相同分数时 rank 不随机或重复。
- 前置：存在相同 routing_score 的两条 route。
- 断言：rank 唯一或使用文档明确的稳定 tie-breaker；重复调用结果顺序一致；不会跳号/重复 rank（除非契约明确允许竞级）。
- 失败级别：P1。

### REC-209 六环境原子性

- 目的：验证 publication 不会只发布部分环境。
- 模式：已有稳定 publication 的只读核查为 R0；创建/发布专用批次为 R1。
- 前置：`LIFE-400` 已确认当前发布模式；有一个完整 active publication，或 R1 可准备一个专用批次。
- DB/API：检查 batch 的六个 `environment_status` 和 route active 指针。
- 断言：若当前文档要求原子发布，active publication 要么包含完整六环境矩阵，要么完全不激活；不能出现新批次只激活 1~5 个环境。若 route 的 `environment_date` 语义在文档中未明确（可能是发布生效日而非 snapshot 日期），先只核对 publication/batch 环境状态并将日期关联列为观察，不据此单独定性。
- 失败级别：P0。

### REC-210 `as_of` 推荐回放

- 目的：验证推荐使用查询时点可见的 forecast 和 publication，而不是最新数据硬覆盖历史。
- 调用：当前时间、历史 publication 切换前后的 `as_of`。
- 断言：历史 as_of 返回当时可见版本；不可见 forecast/publication 不被使用；route 的 batch、revision 和 data_as_of 可回放到 DB。
- 失败级别：P0。

### REC-211 API/MCP 推荐一致性

- 目的：确认两个入口不会给出不同业务答案。
- 调用：Backend recommendations 与 MCP recommendations 同一 scope/profile/as_of。
- 断言：status、reason、forecast label、publication batch、factor_ref、rank、score 一致；只允许 envelope 名称差异。
- 失败级别：P1。

### REC-212 推荐不会触发评估

- 目的：确认推荐查询是读已发布结果，不会临时计算。
- 调用前后：查询评估任务、batch status、指标写入时间。
- 断言：没有新 evaluation task 或指标写入；响应耗时不依赖触发后台计算。
- 失败级别：P1。

### REC-213 历史 active 切换

- 目的：验证新发布失败时旧 active 保持不变。
- 模式：条件 R1。
- 前置：R1 专用批次和已有 active publication；不得操作共享生产数据。
- 断言：失败发布后旧 batch 仍 active；新 batch `publish_status=failed` 或未激活；查询结果无半切换状态。
- 失败级别：P0。

## 11. F1：指标与适用标签

### MET-301 指定因子指标查询

- 目的：验证因子指标查询返回明确 batch、环境和评估类型。
- 调用：按工具 Schema 提供合法的 `factor_ref`、`market_scope`；先省略可选 batch（若 Schema 允许），再传动态发现的 `batch_uid`，最后传随机 UUID。
- 断言：必填参数缺失返回结构化参数错误；指定存在 batch 只返回该 batch，不能静默回退 active；随机 UUID 按文档返回 `NOT_FOUND`/空业务结果。返回 `factor_ref`、batch、items、returned_count 的字段类型按 Schema 校验；每条 item 的 batch 不混合，`label_code` 属于当前六枚举。工具若规定必须带 batch，则省略 batch 的分支按契约判定而非猜测 fallback。
- 失败级别：P1。

### MET-302 时序/截面过滤

- 目的：验证 `evaluation_type` 过滤是真过滤。
- 调用：仅当 MCP-002 确认工具 Schema 声明该参数时，分别传 Schema 枚举中的每个值和不传；否则执行 Backend/DB 的等价拆分对账。
- 断言：只有当工具 Schema/文档声明支持 `evaluation_type` 参数时，过滤调用才必须只返回对应类型；若文档明确该接口不提供此过滤，标记 `NOT_APPLICABLE`，改用 DB/API 分拆对账。声明支持时，不存在类型返回参数错误；TS/CS 字段不可互换，不得把未实现过滤误报为数据缺失。
- 失败级别：P1。

### MET-303 标签过滤

- 目的：验证 `label_code` 过滤不把其它环境混入。
- 调用：逐个使用真实存在和不存在的六类代码。
- 断言：命中项 `label_code` 精确相等；不存在环境返回空集合或明确业务状态；只有 Schema/文档明确禁止旧小写代码时才要求参数错误，否则旧兼容输入仅记录观察，不作为本轮功能缺陷。
- 失败级别：P1。

### MET-304 批次隔离

- 目的：防止指定 batch 后回退到 active 或混合另一批次。
- 调用：指定一个历史 batch、active batch、随机 UUID。
- 断言：指定存在 batch 只返回该 batch；不存在返回 MCP `isError=true`、`NOT_FOUND`；不能静默回退当前 active；错误包含 request id。
- 失败级别：P1。

### MET-305 指标状态语义

- 目的：验证 `success`、`insufficient_sample`、`failed` 的业务含义。
- DB/API：抽取三类状态（若当前数据具备）。
- 断言：`insufficient_sample` 有样本/覆盖不足证据且不进入 route；`failed` 有技术错误且阻止发布；`success` 才能继续 validity 判断；不能把样本不足包装成接口失败或成功推荐。
- 缺少某状态时记录 `BLOCKED`，不要制造状态。
- 失败级别：P0（`failed`/`insufficient_sample` 指标进入 eligible route 或失败批次被发布）；P1（状态、原因或样本证据不一致，但尚未影响发布/推荐）。

### MET-306 指标字段和样本证据

- 目的：核对每条指标可解释且与计算范围一致。
- 断言：按该工具 Schema/接口文档逐项校验声明的 evaluation_type、周期/窗口、样本数、有效样本数、覆盖率、缺失率、起止时间、OOS 和相应 TS/CS 指标；未声明字段不强制要求。显式 null 必须按 nullable 契约保留，不能被客户端丢弃。
- DB：逐字段与 `market_environment_factor_metric` 对账。
- 失败级别：P1。

### MET-307 适用标签查询

- 目的：验证标签只来自单一 active publication。
- 调用：`factor_get_environment_tags`。
- 断言：返回 `factor_ref`、publication、items、returned_count；items 只包含 active route；rank/score/evidence 可回指同一 publication/batch/metric；无 active publication 时 `publication=null` 且空集合，不返回历史 route 冒充当前。
- 失败级别：P1。

### MET-308 因子身份和版本

- 目的：防止母/子因子或版本串线。
- DB/API：对每个返回 factor_ref 核对 factor_type、factor_id/sub_factor_id、factor_version、formula_version。
- 断言：子因子使用自身版本；母因子有冻结的子关系快照；同一 ID 不出现互相冲突的版本身份。
- 失败级别：P1。

### MET-309 指标与 route 外键关系

- 目的：验证推荐证据确实来自同一批次指标。
- DB：检查 route.metric_id/batch_id 与 metric 表，不能只按 factor_ref 猜测。
- 断言：每条 active route 必须有契约规定的指标证据，且 TS/CS 至少一个维度为 `success + is_valid=true`；不要求两个维度同时有效。所有已引用的证据 metric 必须属于 route 的同一 batch、label_code、factor version；重复或多重命中判失败，不静默选第一条。
- 失败级别：P0。

### MET-310 API 与 MCP 指标差异边界

- 目的：确认 Backend API 没有 evaluation_type 过滤不是数据缺失。
- 调用：Backend 同条件返回全量；MCP 分别过滤 TS/CS。
- 断言：Backend 全量集合可拆分重建 MCP 两个集合；业务字段一致；差异仅限明确的 endpoint envelope。
- 失败级别：P1。

### MET-311 无效因子不进入推荐

- 目的：验证实验态、失效、停用因子被排除。
- 前置：从 catalog 动态找状态不满足的因子；只读查询。
- 断言：其 detail 可查询但不出现在 active route/recommendations；若指标存在，状态和排除原因可解释。
- 失败级别：P0。

## 12. L1：批次、评估、发布和回滚（写入门禁）

除明确标为只读的 `LIFE-400` 和 `LIFE-416` GET 外，以下用例只有在 `ALLOW_TEST_WRITES=true`、JWT/HMAC 权限已核验、专用测试 market scope 或可安全清理的输入已准备时执行。否则统一 `BLOCKED`，不把缺少写权限当产品失败。

### LIFE-400 发布模式契约门禁（只读）

- 目的：在任何写入前确认文档与批次配置对“完整原子发布/增量发布”的定义一致。
- 调用：只读读取当前文档、公开 Schema 和动态发现 batch 的 `evaluation_config.publication_mode`、环境终态、publish 状态字段；不调用 POST。
- 断言：必须明确允许的发布时机（running/pending/success）、部分环境/因子是否可见、route 准入条件、publication 身份稳定性、历史保留和重复发布语义。若配置与文档冲突，输出 `BLOCKED_DOC`（blocking_reason=`PUBLICATION_MODE_CONFLICT`），暂停 LIFE-401~418 的写入与正向结论；可继续只读诊断并把实际状态列为观察/候选问题。只有契约确认后才选择对应的 LIFE-410/411/412 预期。
- 失败级别：不直接定产品失败；契约已明确而实现不符时由对应生命周期用例判 P0/P1。

### LIFE-401 创建有效批次

- 调用：Backend `POST /market-environment/eval-batches`，请求包含 `market_scope`、`label_kind=fact`、合法日期、至少一个动态 `factor_ref`、`evaluation_config`、`evaluation_config_version`、`score_rule_version`、`code_version`。
- 断言：HTTP 201、`created=true`、status=`pending`、publish_status=`unpublished`；六类 environment_status 均存在；返回 batch_uid 是 UUID。
- DB：检查冻结的环境/因子/config/code/hash 和创建 request id。
- 失败级别：P1。

### LIFE-402 创建参数校验

- 调用：空 factor_refs、重复 refs、非法日期顺序、超过 3660 天、未知 label_kind/profile、缺少版本字段、未知 JSON 字段、非 UUID batch 等。
- 断言：参数错误为 4xx/结构化错误；不创建半成品 batch；重复 factor_ref 只按文档去重；错误不泄露 SQL/密钥。
- 失败级别：P1。

### LIFE-403 建批次幂等

- 调用：完全相同原始 JSON 重放两次。
- 断言：首次 201/`created=true`，重复 200/`created=false`；两次 batch_uid、快照 hash、状态一致；不会产生第二批次。
- DB：按幂等键查计数。
- 失败级别：P0（重放触发第二条计算/发布链路或覆盖已有业务状态）；P1（仅重复创建未执行的 batch、ID/hash 或幂等响应不一致）。

### LIFE-404 内容冲突拒绝

- 调用：只修改 factor_refs、日期、配置或 code_version 之一，复用同一显式幂等标识（若接口支持）。
- 断言：服务拒绝冲突或生成新的明确批次，不能把不同输入静默映射到旧 batch；审计中记录冲突。
- 失败级别：P1。

### LIFE-405 快照冻结

- 调用：建批次后改变因子关系/新环境 revision（只能通过受控测试 fixture），再读取原 batch。
- 断言：原 batch 的环境 revision、因子版本、母子关系、配置和 hashes 不变；新变化只影响新批次。
- 失败级别：P0。

### LIFE-406 v1 评估启动

- 调用：按 HMAC 规范签名 `POST /factor/environment/evaluations/{batch_uid}`，body `{"force":false}`。
- 断言：HTTP 202、`status=running`、`task_id`、`batch_uid`、`created` 存在；签名使用实际原始 body；时间戳误差在 ±300 秒；nonce 不可重放。
- 失败级别：P0（无效签名/重放被接受，或一次输入启动多条评估链路）；P1（合法请求的状态、业务 ID 或响应契约错误，且未产生重复/越权执行）。

### LIFE-407 评估任务幂等和并发

- 调用：同一 batch 连续/并发启动两次。
- 断言：使用确定性 task ID 或明确幂等响应；不产生重复计算链路；若已有合法运行，不允许另起同批次任务覆盖状态。
- 失败级别：P1。

### LIFE-408 运行状态轮询

- 调用：GET `/factor/environment/evaluations/runs/{task_id}` 轮询到终态，设置总超时和有限重试。
- 断言：运行中返回 task；完成为顶层 `status=completed` 且 result 存在；失败有 error；状态与 batch DB 状态一致；不能因暂时 404 自动新建任务。
- 失败级别：P1。

### LIFE-409 指标矩阵完整性

- DB：统计冻结因子数 × 6 label_code × 2 evaluation_type 的唯一指标单元。
- 断言：每个单元都有 `success` 或 `insufficient_sample`；缺单元、重复单元或 `failed` 阻止发布；计数与 batch 记录一致。
- 失败级别：P0。

### LIFE-410 不完整批次禁止发布

- 前置：`LIFE-400` 已确认当前契约禁止运行中/不完整批次发布；若正式契约明确允许某种增量发布，则本用例按该模式重写预期或标记 `NOT_APPLICABLE`，不能同时套用两套规则。
- 调用：对 pending/running/partial_fail/failed 或缺矩阵 batch 调 publish。
- 断言：返回明确 409/业务错误；旧 active 不受影响；不产生部分 route。
- 失败级别：P0。

### LIFE-411 成功批次原子发布

- 前置：`LIFE-400` 已确认完整六环境原子发布是当前规则，并有专用 success batch；否则 `BLOCKED_DOC`/`BLOCKED_DATA_PRECONDITION`。
- 调用：对 success 且矩阵完整 batch 调 publish。
- 断言：一次事务激活新 publication，六环境统一切换；旧 active 全部关闭；route 的 batch/metric 外键正确；返回已发布 batch。
- DB：在事务后查询 active 计数和版本。
- 失败级别：P0。

### LIFE-412 重复发布幂等

- 前置：`LIFE-400` 已确认重复发布的幂等键、publication UID 和历史保留语义；未确认不得对共享 active 重放。
- 调用：对当前 active batch 重复 publish。
- 断言：业务幂等成功；不新增重复 route/publication；active 结果和版本不变。
- 失败级别：P1。

### LIFE-413 发布失败保留旧 active

- 前置：准备一个会失败的 batch，同时存在旧 active。
- 调用：发布失败 batch。
- 断言：旧 active 仍可查询；新 batch 不得出现部分 active route；失败状态、错误和 request id 可追踪。
- 失败级别：P0。

### LIFE-414 回滚已发布历史

- 调用：回滚到曾经发布过的 batch，再查询 current/recommendations/tags。
- 断言：只有历史已发布目标允许回滚；active 指针整体切换；指标和排名回指目标 batch；历史记录不删除。
- 失败级别：P0。

### LIFE-415 回滚未发布目标

- 调用：回滚 pending、failed 或从未发布 batch。
- 断言：HTTP 409、`ROLLBACK_TARGET_NOT_PUBLISHED`；当前 active 不变。
- 失败级别：P1。

### LIFE-416 Scheduler 健康和默认任务配置

- 前置：`SCHEDULER_BASE_URL` 已明确配置且可访问；否则 `BLOCKED_ENV`。不得从 MCP/Backend host 猜测 Scheduler 地址。
- 调用：4.6 中的 health/jobs GET。
- 断言：health 返回 `success`、`worker_enabled`、`worker_id`、`repo_root`；`market_environment_daily_monitor` 默认启用且时区为 `Asia/Shanghai`、20:30；`market_environment_weekly_evaluation` 默认关闭且为周日 21:00；job 配置与当前代码/文档一致。
- 失败级别：P1。

### LIFE-417 Scheduler 手动运行和幂等

- 前置：`ALLOW_TEST_WRITES=true`，使用专用测试 job 或巡检 job；不能在共享 active 上强制重算。
- 调用：POST `/scheduler/jobs/{job_key}/run`，然后查询 runs、详情和 logs。
- 断言：未知 body 字段被拒绝；运行记录有唯一 run_id、状态、actor、重试/错误信息；同一巡检窗口不重复创建 batch；daily monitor 只巡检，weekly evaluation 才能编排建批次/评估/发布。
- 失败级别：P0（重复发布或越权写入）/P1。

### LIFE-418 Scheduler 运行失败隔离

- 前置：专用测试 job 可控失败。
- 断言：失败 run 有明确阶段和日志；不产生半套 batch/route；旧 active 保持；后续修复重跑不会复制同一幂等输入。
- 失败级别：P0。

## 13. C1：计算与数据正确性

这些用例需要从 DB 读取原始样本、指标和配置；不允许仅凭接口“有数字”就判通过。

### CALC-501 time_series 与 cross_sectional 语义

- 目的：确认两类评估没有互换。
- 核对：TS 应按单资产时间序列；CS 应按同一时点跨资产。检查样本主键、日期/资产维度和指标字段。
- 断言：TS 不把同日其它资产拼为时间序列；CS 不把跨日单资产样本当横截面；两者的样本数、覆盖率和结果可解释不同。
- 失败级别：P0。

### CALC-502 时间排序与连续区间

- 目的：防止不连续日期拼接改变收益、年化收益或最大回撤。
- DB/配置：检查样本日期排序、连续区间数、缺失标签天数和收益定义。
- 断言：连续区间被保留；配置明确采用“非目标环境零暴露”或“按连续区间分别统计”；结果不能把间隔期当持有期。
- 失败级别：P0（错误拼接已改变 active publication 的指标或推荐）；P1（仅历史/未发布指标计算错误，尚未影响在线结果）。

### CALC-503 未来信息泄漏

- 目的：确认 forecast、未来 bar、未来标签和全样本统计量未进入历史结果。
- 核对：对 batch `as_of_time`、environment `available_at`、训练/OOS 起止时间、标准化参数来源做时间比较。
- 断言：任何输入时间晚于可见边界都被排除；训练参数不使用验证/OOS 数据；修改未来样本不应改变过去 batch（可用只读历史 batch 对比）。
- 失败级别：P0。

### CALC-504 环境分类唯一性

- DB：按 market/date 查询事实标签。
- 断言：同一资产同一日期至多一个有效环境；缺失/invalid 单独计数；不会同时归入两个目标环境或被静默复制到所有环境。
- 失败级别：P0。

### CALC-505 样本门槛与 insufficient_sample

- 核对：最低标签天数、有效样本数、覆盖率、OOS 样本和截面资产池。
- 断言：不满足门槛写 `insufficient_sample`；指标保存缺失原因；不生成 eligible route；不会把样本不足当 500 或成功推荐。
- 失败级别：P1。

### CALC-506 路由得分重算

- 核对：从 metric 表读取 TS/CS score、profile、权重、归一化和门槛，使用 Decimal 重算 routing_score。
- 断言：结果与 route 表和 MCP/API 一致；不适用评估类型按配置重新归一；方向和费用假设来自版本化配置，而不是客户端猜测。
- 失败级别：P0。

### CALC-507 排名分区

- 核对：按 `market_scope + label_code + route_profile_key + as_of_date` 分区排序。
- 断言：不同市场/环境/profile 不互相竞争；rank 从正确分区开始；score 越高 rank 越靠前；历史 publication 不混入当前。
- 失败级别：P1。

### CALC-508 成本后指标

- 核对：交易成本配置、换手、费用后收益/Sharpe 和原始收益。
- 断言：成本假设来自 batch evaluation_config；费用后结果不会高于无成本结果（除非有明确符号定义）；空值和零值语义正确；route evidence 可追溯。
- 失败级别：P1。

### CALC-509 数值精度与 null

- 核对：API/MCP JSON、DB Decimal、计算中间值。
- 断言：金额/分数使用 Decimal，并从字段 scale、接口文档或评分配置读取绝对/相对容差；若契约未给容差，数据库定点数和 JSON 十进制字符串按精确规范化值比较，不自行选择 `1e-6` 等阈值。布尔字段与 DB tinyint 语义一致；显式 null 不被转成 0、空串或缺字段；排序不能因浮点字符串比较错误。
- 失败级别：P1。

### CALC-510 因子公式窗口一致性（条件执行）

- 目的：检查因子公式声明的窗口与指标请求/结果窗口一致，特别是 VWAP 等滚动函数。
- 前置：当前工具清单/因子详情必须明确提供公式、窗口和原始样本来源；`factor_get_formula`、`factor_get_metrics` 不是本规约默认存在的工具名，只有动态发现并获文档授权时才调用。
- 方法：动态选择可读取公式的因子，解析不可变公式证据和 `factor_window_bars/window_scope`；对可获取的原始价格/成交量样本使用独立 oracle 计算滚动 VWAP，并与结果对比。
- 断言：公式明确使用与评估窗口一致的滚动窗口；不能把累计 VWAP 当滚动 VWAP；公式版本、指标窗口和 batch config 可互相回指。
- 定性：若发现只影响旧因子公式而不影响 4.0 路由，记录为数据/公式问题，不归因于 MCP 协议；是否纳入当前发布门禁由产品另行确认。缺少公式或独立 oracle 时标记 `BLOCKED_DATA_PRECONDITION`，不猜测数值。
- 失败级别：P0（有独立 oracle 且已影响推荐数值）/P1（单因子影响）。

### CALC-511 母子因子快照

- 核对：建批次时的关系快照、评估时的 factor_ref 和版本。
- 断言：母因子评估输入是创建批次时的全部子因子；关系后来变化不改变旧 batch；子因子直接使用自身数据。
- 失败级别：P1。

### CALC-512 重算可重复性

- 前置：同一输入、同一代码/配置/环境 snapshot。
- 调用：受控重算或读取幂等结果。
- 断言：相同输入的指标、hash、状态和 route 排名可重复；不同 code/config/hash 生成新批次而不是覆盖旧结果。
- 失败级别：P1。

### CALC-513 route 与环境快照引用完整性

- 目的：确认推荐 route 引用的环境、批次和可见性边界真实存在且语义一致。
- 前置：动态发现一个稳定的 batch/publication，并读取其 `environment_snapshot`、`missing_dates`（若有）和 route 字段定义；若文档未明确 `route.environment_date` 是环境样本日还是发布生效日，日期比对只作观察。
- 核对：逐条 route 的 `eval_batch_id`、`publication_uid`、`label_kind`、`market_scope`、`as_of_time` 与 batch/publication 对账；若字段语义明确为 snapshot 成员日期，则不得命中 `missing_dates` 或不存在的 daily current 行。
- 断言：外键和业务引用唯一、可回放；route 不引用不可见/缺失环境或另一个 scope/batch；语义未定义时不把日期差异单独定性，记录待确认项。
- 失败级别：P0（明确违反可见性/外键契约）/P1（可回放信息缺失）。

## 14. D1：数据库、审计与权限

### 14.0 DB 查询注册表与快照规则

Case 不得自行拼接 SQL。AI 执行器必须实现或注入以下语义化 `query_name`；每条查询使用参数绑定、记录执行时间和结果摘要，并在报告中隐藏密码、Token、raw payload 和个人信息。若某个查询或表在当前版本不存在，输出 `BLOCKED_ENV`/`BLOCKED_DOC`，不能用近似旧表替代。

| `query_name` | 用途 | 关键参数/返回摘要 |
| --- | --- | --- |
| `db_identity` | 环境门禁 | `DATABASE()`, `CURRENT_USER()`, host（脱敏） |
| `db_grants` | 权限核对 | 当前用户 grants；区分读账号与测试写账号 |
| `table_schema` | 表/列/索引核对 | 目标表名、列名、约束摘要 |
| `daily_by_key` | environment revision 对账 | `environment_date`, `label_kind`, `revision` |
| `daily_current_by_kind` | current 唯一性 | `label_kind`, 日期范围，current 计数 |
| `batch_by_uid` | 批次状态/快照 | `batch_uid`，状态、计数、publication、快照 hash |
| `batch_metric_aggregate` | 指标矩阵 | batch、factor、label、evaluation_type 的计数/重复 |
| `metric_by_route` | route 外键和准入 | `route.id`/`metric_id`，指标状态、validity、score |
| `active_routes` | active publication | scope/profile/label，route 集合和版本 |
| `publication_history` | 发布/回滚历史 | publication UID、publish version、active/superseded |
| `audit_by_request` | 审计关联 | request/trace/actor/阶段，不返回敏感原文 |
| `business_counts_snapshot` | 无副作用前后快照 | 相关表行数、最大更新时间、任务聚合 |
| `scheduler_jobs_runs` | Scheduler 配置/运行对账 | job key、enabled、cron、timezone、run 状态、业务 ID 和日志引用摘要 |

只读对账必须在尽量短的事务/一致性读窗口内完成并生成 `SNAPSHOT_ID`。如果快照期间发现 batch、metric、route 或 daily 仍在变动，记录变化序列并将依赖终态的用例标为 `BLOCKED`；不能把不同时间点的聚合值相加后与 API 结果比较。

### DB-601 表和关系存在性

- DB：调用 `table_schema` 检查 `market_environment_daily`、`market_environment_eval_batch`、`market_environment_factor_metric`、`market_environment_factor_route` 及当前文档列出的反馈表的表结构、索引和外键/业务唯一约束。
- 断言：表名和关键列符合当前接口文档；不能用技术方案旧表名替代当前表；读取账号无 DDL 权限。
- 失败级别：P1。

### DB-602 daily 唯一约束

- 方法：使用 `daily_by_key` 和 `daily_current_by_kind`，同时读取对应索引/约束摘要。
- 断言：同 `environment_date + label_kind + revision` 唯一，同日期+类型最多一个 current；历史 revision 不物理覆盖。若数据库没有物理唯一约束但服务层有等价保证，必须提供可复现的服务级证据，不得仅凭一次查询判通过。
- 失败级别：P1。

### DB-603 metric 唯一约束

- 方法：使用 `batch_metric_aggregate`，并读取当前表的唯一索引/业务键定义。
- 断言：按实际唯一键（至少 batch、factor_ref、factor_version、label_code、evaluation_type、interval、return_bar_interval、forward_return_bars、window_scope）只有一个正式指标单元；重复计算不会插入重复或随机覆盖；失败重跑有明确 attempt/状态。不要把较短的业务键误当数据库唯一键。
- 失败级别：P0（重复/覆盖记录已被 active route 引用或导致错误发布）；P1（仅未发布批次存在重复或唯一约束缺失，尚未影响在线结果）。

### DB-604 route active 历史

- 断言：发布切换关闭旧 active、开启新 active；历史 route 保留；同一 publication 不出现多个 active 版本；route 必须关联同一 batch 的 metric。
- 失败级别：P0。

### DB-605 API/MCP/DB 三方对账

- 方法：对每个正向或业务空结果，选取业务主键做三方核对。
- 断言：明确返回的字段（包括 null）在 DB 有对应值；API 未暴露字段不自行推断；返回的 request/trace/batch/factor/rank/score 能追溯。
- 失败级别：P1。

### DB-606 审计字段

- 断言：环境同步、建批次、评估、发布、回滚、错误和权限拒绝均有 request id/actor/时间/版本等审计信息；审计记录不包含完整 Token 或密码。
- 失败级别：P1。

### DB-607 事务原子性

- 方法：在专用测试批次中制造一个可控中途失败，然后检查事务前后状态。
- 断言：不能留下半套 route、部分 active 指针或已发布但 batch 失败的矛盾状态；旧 active 保持完整。
- 失败级别：P0。

### DB-608 只读账号权限

- 断言：Agent Data PAT 只能按 scope 读取；普通浏览 PAT 无 `strategy.feedback.write`；Backend 普通 JWT 无 HMAC 内部评估权限；数据库查询账号无写入/DDL 权限（除专用测试写账号）。
- 失败级别：P0。

### DB-609 原始数据敏感信息

- 断言：raw_payload、错误、日志和 JUnit 报告不含 Authorization、密码、完整 JWT/HMAC；业务必要的研究数据按当前数据分级处理。
- 失败级别：P0。

### DB-610 清理和共享数据保护

- 方法：从本次资源清单选择 `RUN_ID` 可证明归属的对象，清理前后读取引用、active 状态和业务计数快照。
- 断言：只清理本次创建且确认归属的测试资源；不得删除已有 active publication、历史审计或他人资源；清理失败必须报告而不是强制删除。
- 失败级别：P0（误删共享数据）/P1（测试资源未清理）。

### DB-611 HMAC/拒绝请求无副作用

- 方法：执行 `HMAC-002` 至 `HMAC-006`，前后查询 batch、task、publication 和审计聚合。
- 断言：所有拒绝请求不新增业务 batch、评估 task 或 active route；必要的安全审计记录不改变业务状态。
- 失败级别：P0。

### DB-612 Scheduler 状态与数据库对账

- 方法：使用 `scheduler_jobs_runs`，将 Scheduler API 的 run、batch、task 和日志中的业务 ID 与对应表聚合结果对账；缺少 `SCHEDULER_BASE_URL` 时保留 DB 旁证，但本用例结果仍为 `BLOCKED_ENV`，不得用 DB 记录代替 HTTP 契约验证。
- 断言：run 状态、batch 状态、task 状态和 publish 状态没有互相矛盾；日志引用的 batch/task 确实存在且属于同一 run。
- 失败级别：P1。

### DB-613 批次、发布与 active route 状态不变量（只读）

- 目的：在不执行发布写入的情况下，检查当前数据库是否存在发布状态自相矛盾或不可审计状态。
- 前置：`LIFE-400` 已从当前契约确认允许的 publication mode 和不变量；使用同一 `SNAPSHOT_ID` 查询，若对象仍在变化则先保存至少两次时间序列。
- 方法：用 `batch_by_uid`、`active_routes`、`publication_history`、`batch_metric_aggregate` 和 `metric_by_route` 核对：batch status/finished_at、publish_status/published_at/is_active、publication_uid/publish_version、六环境状态、指标计数、active route 环境集合和指标 evidence。
- 断言：只应用 `LIFE-400` 确认过的规则。若契约要求完成后原子发布，则 `running/pending + published/active`、`finished_at=NULL + published_at!=NULL`、部分环境 active、publication 身份无历史记录、同一 publish version 身份变化、或 invalid metric 进入 eligible route 均为失败证据；若契约明确允许增量发布，则按其已声明的可见性、身份稳定和回滚规则检查。契约仍冲突时本用例 `BLOCKED_DOC`，但必须保留实际状态时间线，不能判 PASS。
- 失败级别：P0（用户可见错误推荐、部分切换或历史不可回滚）/P1（审计/状态不一致但未暴露错误推荐）。

## 15. AI 执行顺序

执行器必须按以下顺序运行，并在每个阶段结束时生成阶段摘要：

1. **门禁和文档快照**：确认测试域名、Secret、DB、实际 grants、`ALLOW_TEST_WRITES`，读取三份 Lark 文档并生成 `DOC_SNAPSHOT`。
2. **MCP 基线**：执行 `MCP-001` 至 `MCP-019`；若初始化/鉴权失败，后续依赖 MCP 的用例只记录为阻断，不重复报同一根因。
3. **动态发现**：通过 Schema/API/DB 发现工具、因子、环境、批次、publication、profile 和可用查询；固定 `SNAPSHOT_ID`。
4. **只读环境**：执行 `ENV-101` 至 `ENV-111`；`ENV-112` 仅在 R1 测试同步入口存在时执行。
5. **只读推荐/指标**：执行 `REC-201` 至 `REC-206`、`REC-208`、`REC-210` 至 `REC-212` 和 `MET-301` 至 `MET-311`；`REC-207`、`REC-209` 在已有自然数据时可 R0 执行，需要创建 fixture 时转 R1；`REC-213` 仅在专用失败发布 fixture 可用时执行。
6. **计算/三方对账**：执行 `CALC-501` 至 `CALC-513`、`DB-601` 至 `DB-606`、`DB-608` 至 `DB-609`、`DB-613`；后台状态移动时保留时间序列并标记阻断。
7. **HMAC 和写入生命周期**：先只读执行 `LIFE-400`；发布模式契约与 R1 门禁都满足后，执行 `HMAC-001` 至 `HMAC-007`，再执行 `LIFE-401` 至 `LIFE-418`、`DB-607`、`DB-610` 至 `DB-612`。`LIFE-416` 的 GET 健康检查可在内网可达时提前执行，但 POST 手动 run 仍受 R1 门禁。
8. **清理与汇总**：只清理本次 `RUN_ID` 资源，检查共享 active 指针未被改变，生成最终报告和未覆盖清单。

遇到 P0 失败时：

- 立即停止会扩大数据影响的后续写入用例；
- 可以继续执行不依赖该故障的只读诊断；
- 不要用重试掩盖确定性失败；
- 关联同一 request/trace/batch 的重复表现，合并为一个问题而不是重复报多个。

## 16. 单个用例的 AI 执行输出格式

每个用例输出一条结构化记录。下面是字段形状示例，竖线分隔的字符串不是可接受值；执行器必须写入其中一个枚举值：

```json
{
  "case_id": "REC-202",
  "module": "recommendations",
  "mode": "READ_ONLY",
  "status": "BLOCKED",
  "failure_class": "ASYNC_STATE_MOVING",
  "severity": null,
  "preconditions": ["..."],
  "request": {
    "transport": "mcp",
    "tool": "environment_get_recommendations",
    "arguments_redacted": {"market_scope": "<discovered-scope>", "limit": "<schema-default>"}
  },
  "observed": {
    "http_status": 200,
    "jsonrpc_id": "redacted-or-case-id",
    "request_id": "uuid",
    "trace_id": "uuid",
    "status": "no_recommendation",
    "reason_code": "ACTIVE_PUBLICATION_NOT_FOUND"
  },
  "database_evidence": {
    "query_name": "active_publication_by_market_scope",
    "summary": {"active_count": null},
    "snapshot_id": "<snapshot-id>"
  },
  "artifacts": ["reports/factor4/<run>/<case-id>.body"],
  "assertions": [
    {
      "assertion_id": "REC-202-A01",
      "source": "contract",
      "expected": "stable publication precondition",
      "actual": "publication changed during snapshot",
      "result": "BLOCKED"
    }
  ],
  "expected_vs_actual": "无法在同一稳定快照中比较",
  "reproducible": null,
  "blocking_reason": "ASYNC_STATE_MOVING",
  "first_observed_at": "<RFC3339 timestamp>",
  "attempt_count": 2,
  "notes": "依赖对象在执行窗口内仍在异步更新，未形成终态结论"
}
```

执行器必须保证每条记录至少包含：`case_id`、`module`、`mode`、`status`、`failure_class`、`severity`、`preconditions`、`request`、`observed`、`artifacts`、`assertions`、`expected_vs_actual`、`reproducible`、`first_observed_at`、`attempt_count` 和 `notes`。每个 assertion 包含唯一 `assertion_id`、`source`、`expected`、`actual`、`result`；`source` 使用第 3.1 节四类来源，`result` 使用 `PASS/FAIL/BLOCKED/NOT_APPLICABLE`。`BLOCKED` 还必须有 `blocking_reason` 和缺失条件；`FAIL` 必须有可复现请求/响应和至少一条契约或 oracle 断言；`PASS` 不得只凭 HTTP 200，必须列出实际断言结果。

最小枚举约束：

- `status`：`PASS`、`FAIL`、`BLOCKED`、`NOT_APPLICABLE`。
- `failure_class`：仅使用第 5 节定义值；`PASS`/`NOT_APPLICABLE` 为 `null`。
- `severity`：`FAIL` 时为 `P0`、`P1` 或 `P2`，其它状态为 `null`。
- `mode`：`READ_ONLY`、`R1_WRITE` 或 `R2_WRITE`；反馈/共享数据修改只能是 `R2_WRITE` 且需额外授权。
- `reproducible`：仅已执行的 `PASS`/`FAIL` 填布尔值，阻断和不适用填 `null`。

同一执行批次内 `case_id` 必须唯一，每个 `assertion_id` 在所属 case 内唯一，每个 artifact 路径只能属于一个 `case_id + attempt`；生成报告前做全局唯一性校验。一个 case 有多个失败断言时只输出一个 severity，按已确认影响取最高级别（P0 高于 P1，高于 P2），不能输出 `P0/P1` 候选字符串；影响尚未确认时先阻断或按较低级别记录并说明升级条件。

每个执行批次另输出一份 manifest/result 汇总，包含 `run_id`、`doc_snapshot`、`snapshot_id`、开始/结束时间、工具和端点版本、各状态计数、P0/P1 问题去重键、未覆盖原因、异步变化序列、写入资源清理结果和敏感信息扫描结果。请求/响应 artifact 使用相对 `ARTIFACT_DIR` 的路径；报告中不能出现完整 Authorization、JWT、密码、HMAC secret、session ID 或 raw payload 中的个人敏感字段。

最终汇总至少包含：执行时间、代码/文档基线、环境摘要、用例总数、PASS/FAIL/BLOCKED/NOT_APPLICABLE 数量、P0/P1 问题、未覆盖原因、异步状态变化和写入资源清理结果。`active publication` 是否存在、是否完整、是否仍在变化必须以同一快照记录；“没有 active publication”或“有 active 但无合格 route”都是业务前置/业务结果，不得直接统计为 MCP 故障。依赖稳定正向 publication 的用例在前置不满足时设为 `status=BLOCKED`、`failure_class=BLOCKED_DATA_PRECONDITION`，不能把空结果当 PASS。

## 17. QuestTest 代码落地映射

本文首先是 AI 执行规约，不代表现有 pytest 已实现。若要在 QuestTest 中自动化，必须遵守 `tests -> service -> api/db`，按以下职责落地；不得把完整 MCP 流程、SQL 和 HMAC 逻辑塞进一个 Case 文件：

| 层 | 建议模块 | 职责 |
| --- | --- | --- |
| `config` | `config/settings.py`、`config/test.yaml` | 增加 MCP、Backend、internal、Scheduler、R1 门禁和 artifact 配置；日志脱敏 |
| `api` | `api/factor_data_mcp_api.py` | JSON-RPC 初始化、session/protocol header、tools/list/call、JSON/SSE 解析；不做业务断言 |
| `api` | `api/market_environment_api.py` | 当前文档声明的环境、推荐、批次、发布/回滚 Backend endpoint |
| `api` | `api/environment_internal_api.py` | HMAC 原始字节签名、评估 run、Scheduler；POST 默认不重试 |
| `db` | `db/market_environment_repository.py` | 第 14.0 节 query registry、快照查询、只读/测试写账号隔离 |
| `service` | `service/factor4_validation_service.py` | 动态数据发现、API/MCP/DB 编排、轮询、fixture 登记和清理上下文；不写 pytest 断言 |
| `tools` | `tools/evidence.py` | 无业务含义的脱敏 artifact、hash、结果 schema 和唯一性校验 |
| `tests/cases` | `tests/cases/factor4/` | 按 M0/E1/REC/MET/LIFE/CALC/DB 分文件描述场景和简单断言 |

现有 `HTTPClient`、`AuthAPI`、`DatabaseClient` 和 `scripts/run_tests.py` 可以复用；当前尚无 MCP session/SSE、4.0 API、HMAC、Scheduler、4.0 Repository、动态 selector 和三方对账实现。现有测试用例的通过不能计为因子库 4.0 覆盖。

建议实现顺序：先落地 R0 配置、MCP 客户端、Backend 只读 API、Repository 和 evidence；为这些模块写 unit 测试，再实现 `tests/cases/factor4` 的只读集成用例。R1 只有在专用 scope、Secret、清理方案和 `ALLOW_TEST_WRITES=true` 都具备后才实现/启用。公共方法必须有完整类型标注和输入、输出、异常 docstring。
