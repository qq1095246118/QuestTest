# Factor 4.0 生产重新全量回归 — 2026-09-11

后续修复验收（12:21）：**Backend 推荐接口查询有效发布结果返回 HTTP 500** 已定向通过。原 limit=1 请求返回 HTTP 200，正式 Backend/MCP/DB 推荐用例 1 passed，当前为正常空推荐。下面的 691 实例统计仍保留 10:56–12:08 全量执行原始结果，不与定向验收混算。124 个 DB 取样/基线阻断不依赖推荐接口，本次没有重跑。证据见 [修复验收](../reports/factor4-production-recommendation-fixcheck-20260911.json)。

后续 DB 阻断复跑（12:41–13:03）：上述 124 个实例已全部单独重跑，13 failed、109 errors、2 skipped，0 passed。122 个仍被 DB 读取阻断；2 个 CS 场景已读取 DB，随后因组合筛选样本不足跳过。代表性诊断捕获最初查询错误 2013/HY000，随后回滚异常覆盖了原错误位置；不据此认定全部实例同一个内部根因。详见 [124 个阻断实例复跑](factor4-production-blocked-rerun-20260911.md)，原始全量统计仍保留。

## 本轮范围

用户说明昨夜多次发布，要求重新执行全部用例。2026-09-11 10:56（Asia/Shanghai）启动独立新进程，重新收集 691 个参数化实例；包含计算内部、技术及暂缓专项。使用现有生产只读配置和前轮已修正的本地代码；本轮不通过修改断言改变结果，不用旧报告替代新执行结果。

本轮已于 2026-09-11 12:08 左右结束，原始执行耗时 4319.72 秒（约 72 分钟）。691 个实例对应 171 个测试函数、30 个文件；本轮日志与 JUnit 的 691 个唯一节点逐项一致，并与前轮全量集合一致，没有用旧结果替换本轮结果。

| 原始 pytest 终态 | 上轮 | 本轮 | 变化 |
| --- | ---: | ---: | ---: |
| PASSED | 328 | 329 | +1 |
| FAILED | 117 | 115 | -2 |
| ERROR | 113 | 113 | 0 |
| SKIPPED | 133 | 134 | +1 |
| 总实例 | 691 | 691 | 0 |

整套执行已结束，但存在未进入目标业务断言的实例，不能表述为 691 项业务全部验收完成。FAILED/ERROR 数量也不是产品 Bug 数量。

与前轮相比共有 11 个实例终态变化：4 个 children 游标检查由 FAIL 转 PASS；CS invalid 目录统计由 SKIP 转 PASS；2 个 CS 组合筛选由 SKIP 转 FAIL，均是本地 DB 读取异常；4 个 CS 排名分支由 PASS 转 SKIP，当前候选触发 Top/Bottom 重叠但没有排除/补位契约。其余 680 个实例原始终态相同。终态变化本身不能证明是昨夜部署引起。

前轮 122 个本地 DB 取样/基线读取阻断节点本轮全部仍受相同入口/调用链的 `MySQLInterfaceError` 阻断，恢复 PASS 为 0。新增 CS `empty` 的 `summary_sample`、CS `relaxed` 的 `research_catalog_snapshot` 两项，使本轮总计 **124 个实例（15 FAILED、109 ERROR）**未进入目标业务断言。另 4 个 ERROR 仍属于反馈状态能力的 `submission_exists / ProgrammingError`。这些异常不是“生产没有数据”的证据，现有栈也不足以把底层驱动异常统一写成 `TimeoutError`。

原始证据：[JUnit](../reports/factor4-production-rerun-20260911.xml)、[完整日志](../reports/factor4-production-rerun-20260911.log)、[逐节点结果与两轮转换](../reports/factor4-production-rerun-20260911.json)。

```bash
cd /Users/wrh/Downloads/QuestTest
python3 -u -m pytest tests/cases/factor4 --live --env production \
  --include-factor4-internal-calculation --include-factor4-technical \
  --include-factor4-deferred -v --tb=short -rs -o junit_family=xunit1 \
  --junitxml=reports/factor4-production-rerun-20260911.xml
```

## 结果归因与仍未验收的范围

按本轮错误签名复核，仍确认以下六类业务 Bug，没有确认新增业务 Bug。分类不覆盖或改写 pytest 终态；一个 Bug 可影响多个用例，一个用例也可能包含多个问题。

| 本轮分类 | 实例终态与数量 | 解释 |
| --- | --- | --- |
| 已通过 | 329 PASSED | 本轮实际达到用例的通过条件 |
| 含已确认业务 Bug | 22 FAILED、5 SKIPPED | 下列六个固定标题；TS 公开 scope 阻塞在用例中表现为依赖跳过 |
| 本地 DB 取样/基线读取阻断 | 15 FAILED、109 ERROR | 124 个实例未进入目标业务断言；不是 124 个独立数据库根因 |
| 本地协议/参数判定问题 | 45 FAILED | 43 个合法工具错误文本的 JSON-only 解析问题，加 SSE、版本协商判定各 1 个 |
| 生产反馈状态能力不可用 | 15 FAILED、4 ERROR | 相关工具/表能力缺失，反馈状态链路尚不能验收 |
| 待明确返回语义/追溯及历史时间契约 | 12 FAILED | 推荐发布边界、unknown/null、重复 refs、增量环境状态、helper 和历史记录时间；不新增强制产品契约 |
| 技术审计字段观察 | 4 FAILED | request_id 追溯字段；不计入核心业务 Bug |
| 批量公式详情响应限制 | 2 FAILED | 大批量 executable 读取被限制，不等于单个因子不可读 |
| 样本、证据或契约缺口 | 127 SKIPPED | 不能计为通过，也不单凭缺口新增产品 Bug |
| 当前无状态 MCP 不适用 | 2 SKIPPED | 没有 Session ID，不适用有状态 session 校验 |

四类环境 `UNILATERAL_UP、NARROW_RANGE、UNILATERAL_DOWN、CHOPPY_DOWN` 的候选集合排除对账通过，但当前线上均没有 eligible route，不能执行非空路由公式闭环。`CHOPPY_UP` 有 21 条、`WIDE_RANGE` 有 74 条 eligible route；身份、指标/路由/摘要和双维度无效排除检查通过，冻结 TS/CS 权重及 metric 到不可变公式/schema 的强版本链接仍不足，不能宣称得分到公式闭环全部验收完成。当前推荐正常空结果通过也不代表非空推荐排序已通过。

其他主要缺口包括：准入阈值端点、null/fallback/clip 的自然样本；母子聚合和关系版本证据；取消/回滚终态样本；同分与 Top/Bottom 重叠的明确规则；部分历史公式 completed/source 证据。内部重算、原始持仓或未来扰动等缺口仅保留在用户要求的本次全量专项中，不扩大为“仅对计算结果到输出负责”的核心结果级必补项。

逐项归因：[本轮分类](../reports/factor4-production-rerun-adjudication-20260911.json)、[协议及业务参数复核](../reports/factor4-production-rerun-protocol-review-20260911.json)、[公式/环境/评分/闭环复核](../reports/factor4-production-rerun-formula-review-20260911.json)。

## 本轮独立复现

以下是本轮新请求，不使用昨夜响应作证据；独立诊断不增加正式实例数量。

| 固定中文标题 | 本轮证据 |
| --- | --- |
| Backend 推荐接口查询有效发布结果返回 HTTP 500 | limit=1 请求仍 HTTP 500；request_id=a718633a-15bc-4aff-a8d9-d45b2443f890 |
| Backend TS 指标最小查询超时 | limit=1、单次无重试 60.349 秒 ReadTimeout，无 HTTP 响应 |
| DPO 公式错误地位移均线而非价格序列 | sub_factor:161106 当前 MCP 与 DB detail 169781 仍移动均线 |
| IV/RV 因子定义与实际执行公式及输入字段不一致 | 161628/161629/161630 当前执行逻辑仍为 close 滚动标准差，只有 close 输入 |
| 环境指标最小分页查询仍超过响应大小限制 | 149 / batch7 / WIDE_RANGE / time_series / limit1 仍 RESPONSE_TOO_LARGE；request_id=60056a00-0555-4bfa-8fe7-436757ed2d0d；tags 同错 |
| TS 指标范围查询超时，使用端链路无法开始 | 当前 as_of=2026-09-11T03:13:27.539366+00:00、limit1，20.229 秒 QUERY_TIMEOUT；request_id=650df909-714d-4166-a73c-1a66adefd5e9 |

新证据：[Backend](../reports/factor4-production-backend-diagnostic-20260911-rerun.json)、[MCP 与精确 DB 核查](../reports/factor4-production-mcp-diagnostic-20260911-rerun.json)。公式结论仍限定当前定义及可执行投影，不据此断言缺少强版本绑定的所有发布数值错误。

### 复现公共前置

使用项目现有 `production` 配置。Backend 请求使用独立的 Backend 登录凭据/JWT；MCP 请求使用配置中的 MCP Bearer Token，两者不能互换。报告不展示凭据。

MCP 地址为 `https://factor-frontend.questvector.ai/mcp/factor-data`。先发送 `initialize`，再发送 `notifications/initialized`；本轮实际协商版本为 `2025-06-18`、服务版本为 `factor-agent-data-service 0.2.0`。后续向同一地址 POST 下方完整 JSON-RPC 请求体，带 `Content-Type: application/json`、`Accept: application/json, text/event-stream`、`MCP-Protocol-Version: 2025-06-18` 和配置中的鉴权 Header。本轮未分配 Session ID；重新握手若返回 Session ID，应随请求复用。文中的 request_id 是本轮服务端返回的定位标识，重新调用会产生新值。

### Backend 推荐接口查询有效发布结果返回 HTTP 500

复现：使用 Backend 鉴权发送一次下方 GET，超时设为 60 秒，不自动重试；检查 HTTP 状态与业务信封。

```http
GET https://factor-backend.questvector.ai/api/v1/market-environment/recommendations?market_scope=all&route_profile_key=default&limit=1
```

预期：正常返回推荐数据或业务空结果。实际：约 1.476 秒返回 HTTP 500、`success=false`，服务 request_id 为 `a718633a-15bc-4aff-a8d9-d45b2443f890`。本轮精确 DB 查询确认 `all/default` 存在 active、published 的 batch 7。该证据确认 Backend 查询失败，不保证当前推荐必须非空，也不扩大为 MCP 推荐入口故障；本轮脱敏诊断未保留具体业务错误码。

### Backend TS 指标最小查询超时

复现：使用 Backend 鉴权发送一次下方 GET，读取超时设为 60 秒，不自动重试；记录是否收到 HTTP 响应和耗时。

```http
GET https://factor-backend.questvector.ai/api/v1/factor-ic/summary-metrics?is_sub_factor_id=true&ic_scope=time_series&limit=1
```

预期：单条 TS 指标读取能够正常返回数据或业务空结果。实际：60.349 秒触发 `requests.exceptions.ReadTimeout`，没有收到 HTTP 响应，因此没有服务 request_id 或服务错误码。这里只确认正常最小查询超过客户端读取时限，不能写成服务端返回 `QUERY_TIMEOUT`，也未证明与 MCP TS 查询是同一个内部 SQL 根因。

### DPO 公式错误地位移均线而非价格序列

复现：完成 MCP 握手后发送下方 `tools/call`，找到 `data.items` 中 `factor_ref=sub_factor:161106` 的 `data.calc_logic`。本轮将 DPO 与下一个 IV/RV 问题的三个因子放在同一次批量请求中；两项共用这一份原始响应。

```json
{"jsonrpc":"2.0","id":"formula-repro","method":"tools/call","params":{"name":"factor_get_details_batch","arguments":{"factor_refs":["sub_factor:161106","sub_factor:161628","sub_factor:161629","sub_factor:161630"],"detail_level":"executable"}}}
```

预期：按现有负 DPO 约定，`n=60` 时为 `mean(close, 60) - close.shift(31)`，移动价格序列。实际：HTTP 200、各项 `success=true`，但 `161106` 返回 `-(close - mean(close, (60 + 0)).shift(((60 + 0)) // 2 + 1))`，移动的是均线；本轮 DB 最新 detail `169781` 的表达式相同。服务 request_id 为 `cdb09a64-15ec-4127-aa03-40d80183b261`。确认范围仅当前 `161106` 定义，不扩展到未读取的其他 DPO 因子或缺少版本绑定的已发布数值。

### IV/RV 因子定义与实际执行公式及输入字段不一致

复现：执行上一项完整 `factor_get_details_batch` 请求，分别找到 `161628`、`161629`、`161630`，对比各项 `data.formula_summary`、`data.calc_logic` 与 `data.params.fields`。三者分别对应 30d、7d、90d。

预期：声明为 `factor = ATM_IV - realized_vol`，执行公式及输入应包含相应 IV 输入和 IV 减 RV 运算。实际：三个因子的 `calc_logic` 都为 `close.pct_change().rolling(window).std()`，`params.fields` 都只有 `["close"]`；DB 最新 detail `170303/170304/170305` 同样如此。服务 request_id 为 `cdb09a64-15ec-4127-aa03-40d80183b261`。这是一个问题的三个期限样本；本轮确认定义与执行表达式不一致，不声称已独立重算所有历史因子值或排名。

### 环境指标最小分页查询仍超过响应大小限制

复现：完成 MCP 握手后发送下方请求，已固定单因子、batch 7、`WIDE_RANGE`、TS 维度和最小 `limit=1`；检查 `isError` 和业务错误码。

```json
{"jsonrpc":"2.0","id":"environment-metric-repro","method":"tools/call","params":{"name":"factor_get_environment_metrics","arguments":{"factor_ref":"sub_factor:149","market_scope":"all","route_profile_key":"default","batch_uid":"3603f724-9a14-4c6a-a670-aef51dc19c12","label_code":"WIDE_RANGE","evaluation_type":"time_series","limit":1}}}
```

预期：返回该精确条件下的单条指标。实际：HTTP 200、`isError=true`、`RESPONSE_TOO_LARGE`，上限为 `2621440` 字节，服务 request_id 为 `60056a00-0555-4bfa-8fe7-436757ed2d0d`。本轮 DB 精确查询仅命中 metric `2381`，`metric_status=success`、`is_valid=1`，其 payload 为 `38796` 字节；没有读取整段 payload。当前公开 schema 无字段裁剪参数，不能再通过缩小 limit 规避。

同一因子的 tags 读取也可复现该影响：

```json
{"jsonrpc":"2.0","id":"environment-tags-repro","method":"tools/call","params":{"name":"factor_get_environment_tags","arguments":{"factor_ref":"sub_factor:149","market_scope":"all","route_profile_key":"default"}}}
```

实际同为 `RESPONSE_TOO_LARGE`，服务 request_id 为 `7525edee-dd1e-4715-a56a-2ca881e5ddce`；tags 没有分页或字段裁剪参数。将此记为同一问题的另一影响点，尚未定位响应膨胀字段，也未证明两个端点内部根因相同。

### TS 指标范围查询超时，使用端链路无法开始

复现：完成 MCP 握手后发送下方请求，保留本轮 `as_of` 可核对相同参数；验证当前状态时可换成执行时的带时区时间，其他选择条件保持不变。

```json
{"jsonrpc":"2.0","id":"ts-scope-repro","method":"tools/call","params":{"name":"factor_list_metric_scopes","arguments":{"kind":"sub_factor","ic_scope":"time_series","interval":"1h","universe_key":"all","as_of":"2026-09-11T03:13:27.539366+00:00","limit":1}}}
```

预期：返回最多一个可用 TS 指标范围或正常业务空结果，使使用端能够继续选择范围、查询和排名。实际：20.229 秒返回 HTTP 200、`isError=true`、`QUERY_TIMEOUT`，`details.dependency=reader_db`、`details.timeout_seconds=20`；服务 request_id 为 `650df909-714d-4166-a73c-1a66adefd5e9`。本轮精确 DB 查询取得因子 149 的 `1h/all/time_series` 记录，只用于证明存在 TS 数据，不代替公开范围发现结果，也不声称所有 TS 指标查询端点均不可用。

## 发布状态观察

本轮开始后的 11:07:27 和结束后的 12:10:06 各采集一次小字段状态，两次投影完全相同：all/default active batch7，partial_fail / per_factor_incremental / published，publication_uid=271aab9c-d7d5-4993-ba1e-3aa3ce2b6013；CHOPPY_UP 21、WIDE_RANGE 74，共95条 active eligible route。批次及 route 聚合的版本、身份、时间、数量和排名端点没有观察到变化。

起始采样发生在用例开始后约 12% 进度；仅两点相同不能证明期间没有应用部署、发布后回退、未投影字段变化或其他中间状态，更不能据此解释失败根因。

[起始状态](../reports/factor4-production-rerun-state-start-20260911.json)、[结束状态](../reports/factor4-production-rerun-state-end-20260911.json)、[两点比较](../reports/factor4-production-rerun-state-comparison-20260911.json)。DB 前置的精确节点与驱动异常见 [本轮 DB 分类](../reports/factor4-production-rerun-local-db-classification-20260911.json)。
