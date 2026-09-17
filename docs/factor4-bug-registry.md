# Factor 4.0 Bug Registry

本文件是 Factor 4.0 测试问题的唯一命名登记表。登记时间：2026-09-04（Asia/Shanghai）。

2026-09-06 迁移说明：以下状态、数量和历史数据仍是登记日的记录，不代表本次迁移回归结果。
本次只更新已删除脚本的复现入口，保留固定中文标题及历史证据；当前执行结果见 `docs/factor4-script-migration.md`。
新版命令读取现有测试配置并动态发现数据库样本，不依赖历史报告中的固定 Run。

2026-09-07 全区间历史补齐新增一项 `CANDIDATE`：“环境快照缺失日期与当时可用环境记录不一致”。下方历史数量表不据此当作全量最新验收统计；新项详情单独保留真实证据和未确认的选择契约。

2026-09-07 额度恢复后结果级复跑已结束：369 passed、6 failed、0 errors、263 skipped（139 历史暂缓、124 其他阻塞），没有额度错误。
本次失败已区分候选、正式追溯证据缺口、QA 证据缺口和判定器问题，不直接计为六个产品 Bug。
最新结果及每项复现见 [本轮复跑记录](factor4-result-live-20260907-rerun.md)；下方“当前数量”仍为历史登记口径，不用于这次统计。

## 2026-09-14 全量续跑完成后的最新状态

本次全量经用户暂停后续跑完成，171函数、691唯一实例、30文件；按本日每节点最新正式结果汇总为 **403 PASSED / 84 FAILED / 9 ERROR / 195 SKIPPED**。所有节点均有本轮终态，没有使用9月11日结果填补，也不将跳过算通过。原暂停计数漏了含空格参数的1个PASS，已从596校正为597。完整逐文件、逐节点结果及复现见 [本轮完成报告](factor4-production-full-completed-20260914.md)。本节是生产最新验收口径，下方旧日期结果保留历史含义。

当前确认未关闭4个业务标题，其中新增1个：

- **Backend TS 指标最小查询超时**：固定名称不变；本轮有效登录下最小请求46.134秒HTTP500、查询失败，症状由旧ReadTimeout改变。
- **DPO 公式错误地位移均线而非价格序列**：161106当前执行公式仍位移均线，19:38—19:40复验；仅当前定义证据。
- **IV/RV 因子定义与实际执行公式及输入字段不一致**：161628/161629/161630当前定义仍与close滚动标准差执行式冲突；仅当前定义证据。
- **环境标签查询遗漏当前已发布的有效路由**：新增，详情如下。

原精确请求验收通过：**TS 指标范围查询超时，使用端链路无法开始**（历史/当前as_of的1h/all最小请求均正常）；**环境指标最小分页查询仍超过响应大小限制**（原batch/因子/label/维度精确返回原metric2381）。后者当前tags原样本为空，不能扩展为原非空tags响应已独立验收。**Backend 推荐接口查询有效发布结果返回 HTTP 500**本轮正式用例继续通过。

本地running增量发布选择器过严导致的3ERROR/3生命周期FAIL不计业务Bug；feedback表前置4ERROR、DB取样errno3024的2ERROR+2FAIL、JSON-only错误解析、规范/兼容性及历史DEFERRED继续分开记录。细节和证据边界见本轮报告，原始终态不因裁决改成PASS。

### 环境标签查询遗漏当前已发布的有效路由

- 英文索引：`F4-ENV-TAGS-MISSING-PUBLISHED-ELIGIBLE-ROUTE`；状态：`CONFIRMED`；环境：生产；登记日期：2026-09-14。
- 模块：MCP环境标签读取。当前有效路由已经按因子增量发布，MCP同publication却返回空标签，使用者无法发现该因子的WIDE_RANGE路由。
- 复现：完成生产MCP初始化，调用`factor_get_environment_tags`，参数`{"factor_ref":"sub_factor:1435649","market_scope":"all","route_profile_key":"default"}`。
- 预期：包含该因子的有效WIDE_RANGE route；实际HTTP200/isError=false、returned_count=0/items=[]，publication仍对应batch11。
- DB前后核查：19:42:14—19:42:17批次/route身份稳定，batch11为running/per_factor_incremental/published/active；route14580同ref、WIDE_RANGE、active=1/eligible=1、rank5、score78.856103。关联metric9918同batch/ref/label、CS/success/is_valid1、无error_code；公开detail及名称search均返回相同因子。
- request_id：`717565ea-ea74-4491-967c-9618e869ebb4`；publication_uid：`df8596a9-463e-4d87-bf97-17d8af27b58e`。
- 正式入口：`tests/cases/factor4/test_migrated_readonly_scripts.py::test_environment_tags_match_active_routes`，使用`--live --env production`；自然取样会随发布变化，精确复现使用上方原参数。
- 证据：[本轮原请求及DB前后核对](../reports/factor4-production-resume-error-triage-20260914-review.json)。不能因为running而把合法增量发布排除；这与本地批次选择器过严是两个分别取证的问题。


## 2026-09-11 本地取样优化后的定向回归

后续最小复核已完成：上述 124 实例中的 9 个 FAIL 未新增确认业务 Bug。4 个有效性状态差异的代表请求证实仅 null/unknown 表达不同，成员/指标/统计一致；3 个历史 scope 差异归为“历史 TS 指标范围查询的修订可见性规则不明确”，因当前记录在历史 as-of 之后已更新，尚不能证明旧时点漏数据；“Slice 结束时间边界错误”1 个仍复现但维持用户指定 DEFERRED；“参数校验错误的文本响应被误判为 MCP 返回格式错误”1 个为既有本地解析问题。原始 9 FAIL 终态不改为 PASS，既有中文 Bug 名称和未复验的历史问题状态不变。完整请求和裁决见 [本轮最小复核](factor4-sampling-adjustment-20260911.md#失败现象的后续最小复核)。

本地取样改为覆盖索引候选发现及精确范围完整对账，异常清理保留最初错误。8 类生产 Repository 取样均成功；随后重新执行此前 124 个阻断实例（25 函数、6 模块），结果为 **96 passed、9 failed、0 errors、19 skipped**，耗时约 6 分钟。本地 DB 取样/基线阻断及搜索预算耗尽均为 0。剩余失败为有效性成员状态差异 4 个、历史 scope 数量差异 3 个、切片边界错误 1 个、切片响应解析失败 1 个；这些是待核实断言现象，不直接新增 9 个产品 Bug，也不变更既有固定中文标题。跳过保留样本及契约缺口。详见 [取样优化与本轮结果](factor4-sampling-adjustment-20260911.md)。此次使用动态新样本，不据此关闭未做精确复现的历史问题；以下各轮原始结果保留。

## 2026-09-11 13:03 DB 阻断实例复跑

在推荐 HTTP 500 已验收修复后，124 个 DB 阻断实例已单独重跑，结果为 13 failed、109 errors、2 skipped，0 passed。122 个仍在相同 DB 入口/驱动异常签名处阻断；两个 CS 组合筛选已读到 DB 基线，但缺独立双指标筛选样本，尚未发送目标 MCP 筛选请求。本次没有新增业务结果 Bug，也不将不同轮次混入下方 691 实例原始统计。代表性只读诊断进一步捕获查询阶段的 2013/HY000 连接丢失，随后回滚报错掩盖了最初异常；具体断开原因尚未定位。详见 [DB 阻断复跑结果](factor4-production-blocked-rerun-20260911.md)。

## 2026-09-11 日间重新全量执行

按用户要求在昨夜发布后重新启动一轮独立生产回归，10:56–12:08 执行全部 171 个测试函数 / 691 个实例，最终 **329 passed、115 failed、113 errors、134 skipped**。所有实例均来自本轮新进程，没有用旧结果填补；业务验收仍存在阻断，不能称为全部通过。

本轮新请求再次确认原有六个固定中文标题，名称不变：

- Backend 推荐接口查询有效发布结果返回 HTTP 500
- Backend TS 指标最小查询超时
- 环境指标最小分页查询仍超过响应大小限制
- TS 指标范围查询超时，使用端链路无法开始
- DPO 公式错误地位移均线而非价格序列
- IV/RV 因子定义与实际执行公式及输入字段不一致

前轮 122 个 DB 取样/基线阻断实例本轮未恢复，另有 2 个 CS 基线读取失败，总计 124 个（15 failed、109 errors）；不作为 124 个产品 Bug。四个 children 游标用例本轮恢复通过，不能再沿用上轮该项失败证据。本轮完整统计、请求复现、最新 request_id、样本/契约缺口及两轮转换见 [重新全量结果](factor4-production-rerun-20260911.md)。以下 2026-09-10 与测试环境记录保留历史口径。

## 2026-09-10 生产回归新增固定名称

2026-09-11 全量结束：171 个测试函数、691 个实例，最终 328 passed、117 failed、113 errors、133 skipped。逐节点复核确认 6 类业务 Bug（包含下方新名称及历史 DPO、IV/RV 两个原名称）；原始失败数量不等于 Bug 数量，样本/契约缺口及本地用例问题另列。当前完整结果与复现步骤见 [生产全量结果](factor4-production-full-20260910.md)，不改写下方测试环境历史数量。

### Backend 推荐接口查询有效发布结果返回 HTTP 500

- 状态：FIX_VERIFIED，生产；2026-09-11 12:21（Asia/Shanghai）在用户告知修复后定向验收。原最小请求单次 2.026 秒返回 HTTP 200、success=true；Backend/MCP/DB 推荐对账正式用例 1 passed。当前为 NARROW_RANGE / NO_ELIGIBLE_FACTOR / items=[]，验收范围是原 HTTP 500 及当前空结果一致性，不代表非空推荐排名已覆盖。
- 最新证据：[修复验收](../reports/factor4-production-recommendation-fixcheck-20260911.json)、[正式 JUnit](../reports/factor4-production-recommendation-fixcheck-20260911.xml)。此前 691 实例报告保留原始结果；124 个本地 DB 取样/基线阻断不依赖此推荐端点，本次未重跑，不能随此条修复一并关闭。
- 修复前，有效 Backend 身份下，`GET /api/v1/market-environment/recommendations?market_scope=all&route_profile_key=default&limit=1` 返回 HTTP 500；数据库存在 active published 的增量发布 batch 7。指定 as_of、limit 200 或 1，以及省略 as_of 均重现。
- 预期：返回对应发布推荐，或有明确业务语义的正常空结果；不能对有效读取请求返回内部错误。
- 实际：`success=false`，`error=操作失败，请稍后重试`。最小无 as_of 请求的 request_id：`54c2c6c7-1716-4603-a99b-a3468e2ffc85`。
- 影响：Backend 推荐读取无法完成；不直接推断 MCP 推荐也失败，底层根因待服务日志定位。
- 正式入口：`tests/cases/factor4/test_backend_three_way_business.py::test_backend_recommendations_publication_order_and_scores_match_mcp_database`，参数 `--live --env production`。
- 记录及完整脱敏证据见 [生产回归记录](factor4-production-full-20260910.md)。不改写下方历史测试环境数量。

### 环境指标最小分页查询仍超过响应大小限制

- **英文索引**：`F4-ENV-MINIMUM-READ-RESPONSE-TOO-LARGE`
- **状态 / 环境 / 登记日期**：`CONFIRMED` / 生产 / `2026-09-11`（Asia/Shanghai）。这是使用端单因子正常查询被阻断的功能问题，内部根因尚未定位。
- **问题说明**：用户指定一个真实因子、一个已发布批次、一个环境标签、一个 TS/CS 维度，并将 `limit` 设为最小值 `1`，仍只能收到 `RESPONSE_TOO_LARGE`，无法取得这条已经存在且有效的指标。实时工具 Schema 没有 `fields`、`detail_level` 等可进一步缩小输出的参数，不能通过正常调整请求避开。
- **数据库证据**：生产 `all/default` 当前 active published batch `7`，`batch_uid=3603f724-9a14-4c6a-a670-aef51dc19c12`，状态 `partial_fail`，冻结配置明确声明 `publication_mode=per_factor_incremental`。以下请求唯一对应 `market_environment_factor_metric.id=2381`：`factor_ref=sub_factor:149`、`label_code=WIDE_RANGE`、`evaluation_type=time_series`、`metric_status=success`、`is_valid=1`。该行 `metric_payload` 为 `38,796` 字节；本次只读查询读取身份、状态和 `OCTET_LENGTH`，未拉取完整 payload。批次其他指标失败不改变这条指标已经成功且有效的事实。
- **复现步骤**：使用现有独立生产配置连接 `https://factor-frontend.questvector.ai/mcp/factor-data`，正常完成 MCP `initialize` 和 `notifications/initialized`；然后发送以下完整 `tools/call` 请求。生产鉴权从本机配置读取，不在报告中写入完整 Token。

  ```json
  {
    "jsonrpc": "2.0",
    "id": "env-minimum-metric-repro",
    "method": "tools/call",
    "params": {
      "name": "factor_get_environment_metrics",
      "arguments": {
        "factor_ref": "sub_factor:149",
        "market_scope": "all",
        "route_profile_key": "default",
        "batch_uid": "3603f724-9a14-4c6a-a670-aef51dc19c12",
        "label_code": "WIDE_RANGE",
        "evaluation_type": "time_series",
        "limit": 1
      }
    }
  }
  ```

- **预期 / 实际**：预期成功返回上述唯一指标，保留其批次和维度身份；实际 `HTTP 200`、工具 `isError=true`、`error.code=RESPONSE_TOO_LARGE`、`error.details.max_response_bytes=2621440`（`2,621,440` 字节），没有指标结果。该精确请求耗时 `0.835` 秒，不属于读取超时。原正常基线仅传单因子和 `limit=1` 时也复现，失败发生在异常参数循环之前。
- **同类影响点：环境标签查询**。同一因子的下列正常请求也返回 `HTTP 200`、`isError=true`、`RESPONSE_TOO_LARGE`。实时 `factor_get_environment_tags` Schema 只允许 `factor_ref / market_scope / route_profile_key`，没有分页、标签筛选或字段裁剪参数，因此也没有可用的请求缩小方式。

  ```json
  {
    "jsonrpc": "2.0",
    "id": "env-minimum-tags-repro",
    "method": "tools/call",
    "params": {
      "name": "factor_get_environment_tags",
      "arguments": {
        "factor_ref": "sub_factor:149",
        "market_scope": "all",
        "route_profile_key": "default"
      }
    }
  }
  ```

- **正式用例入口**：`tests/cases/factor4/test_protocol_deferred_business.py::test_protocol_schema_legal_and_invalid_boundaries[factor_get_environment_metrics]`、`[factor_get_environment_tags]`，使用 `--live --env production --include-factor4-deferred`。虽然入口属于协议专项，本条仅确认其最初合法业务读取失败，不把后续未执行的边界断言计为覆盖。
- **证据**：[最小请求与 Schema/DB 摘要](../reports/factor4-production-selector-response-size-20260910.json)、[6 节点定向 JUnit](../reports/factor4-production-selector-rerun-20260910.xml)、[定向日志](../reports/factor4-production-selector-rerun-20260910.log)。报告文件保留原 `20260910` 名称，定向结果仍为 `3 passed / 3 failed`；本条不改写原始执行状态，也不把另一个推荐负向文本解析失败并入产品 Bug。
- **结论边界**：这是单因子、最小合法读取仍不可用，不是批量导出预算或 50 因子请求过大。尚未定位响应膨胀来源，不能仅凭单行 payload 大小推断服务端返回了全部批次数据；指标和标签暂登记同一功能问题的两个影响点，不宣称已证明同一内部根因。

### TS 指标范围查询超时，使用端链路无法开始

- **英文索引**：`F4-TS-METRIC-SCOPE-QUERY-TIMEOUT`
- **状态 / 环境 / 登记日期**：`CONFIRMED` / 生产 / `2026-09-11`（Asia/Shanghai）。这是正常单次业务读取被阻断，未执行压力或性能专项。
- **问题说明**：使用端先查询可用 TS 指标范围，再根据返回的维度进行因子搜索或排名。生产 `factor_list_metric_scopes` 对真实存在的 `sub_factor / time_series / 1h / all` 范围返回服务端数据库查询超时；即使只请求 `limit=1` 也不能返回可用范围，后续用户链路无法开始。
- **真实数据前置**：只读数据库按截至时点最近完成的 Run 取样，`ts_symbol` 和 `ts_aggregate` 均返回真实 scope，耗时分别为 `2.320` 秒、`2.475` 秒。两者都是 `time_series / direct / 1h / factor_window_bars=5 / return_bar_interval=1h / forward_return_bars=1 / universe_key=all / window_scope=min_window / scoring_version=v20260820_ts_rolling_blocks`；币种样本为 `TAOUSDT`，聚合样本为 `symbol=''`。本条不依赖全表统计，也不是无 TS 数据。
- **复现步骤**：使用独立生产配置连接 `https://factor-frontend.questvector.ai/mcp/factor-data`，完成 MCP `initialize` 和 `notifications/initialized`，然后发送以下最小请求。鉴权读取本机生产配置，不在报告中重复完整 Token。

  ```json
  {
    "jsonrpc": "2.0",
    "id": "ts-scope-timeout-repro",
    "method": "tools/call",
    "params": {
      "name": "factor_list_metric_scopes",
      "arguments": {
        "kind": "sub_factor",
        "ic_scope": "time_series",
        "interval": "1h",
        "universe_key": "all",
        "as_of": "2026-09-10T16:04:13.196246+00:00",
        "limit": 1
      }
    }
  }
  ```

- **预期 / 实际**：预期按范围返回最多一条可用 scope；实际耗时 `20.205` 秒后返回 `HTTP 200`、`isError=true`、`error.code=QUERY_TIMEOUT`、`error.message=Database query exceeded the configured execution time`、`error.details.dependency=reader_db`、`error.details.timeout_seconds=20`、`retryable=true`。`request_id=8d47d543-d0ae-49b0-9d39-4a8d2780817a`。相同请求将 `limit` 改为 `100` 时也失败，耗时 `20.260` 秒，`request_id=d77abc33-18c2-4d8d-b7d8-7341cb8b2ec9`。
- **正式用例入口**：`tests/cases/factor4/test_consumer_journey_business.py::test_public_scope_selection_replays_selected_result_and_evidence` 的 `[ts_symbol-search]`、`[ts_symbol-rank]`、`[ts_aggregate-search]`、`[ts_aggregate-rank]`，参数 `--live --env production`。四条正式执行保持 `SKIPPED / BLOCKED_DEPENDENCY: QUERY_TIMEOUT`，人工裁决为已确认服务功能阻塞；不修改为通过，不声称搜索、排名、metrics、validity、formula、slices 已完成验证。
- **证据**：[4 节点 JUnit](../reports/factor4-production-consumer-ts-rerun-20260910.xml)、[完整命令与最小请求记录](../reports/factor4-production-consumer-ts-rerun-20260910.log)。文件名称沿用父任务 `20260910`，实际定向执行开始于 `2026-09-11T00:00:21+08:00`。
- **结论边界**：原本本地 `initial_scope` 的全量 JOIN 排序问题已修正，本条来自随后独立的公开 MCP 正常请求。当前只能确认服务端 reader_db 查询超时，不能据此断言内部 SQL/索引根因或因子排名数值错误；也不重开历史已关闭的“币种级 time-series 排名完全不可用”。

### Backend TS 指标最小查询超时

- **英文索引**：`F4-BACKEND-TS-MINIMUM-SUMMARY-TIMEOUT`
- **状态 / 环境 / 登记日期**：`CONFIRMED` / 生产 / `2026-09-11`（Asia/Shanghai）。这是正常单次最小指标读取未能返回，不是压力或性能专项。
- **问题说明**：用户请求一条子因子的 TS 汇总指标，Backend 对合法 `ic_scope=time_series / is_sub_factor_id=true / limit=1` 查询持续不能在客户端 60 秒时限内返回。公开 OpenAPI 明确支持这三个参数，`factor_id` 为可选项；本次没有发送未知筛选字段。
- **复现步骤**：使用独立生产 Backend JWT，在同一个客户端依次执行下面两条只读请求，关闭自动重试并设单次读取超时为 60 秒。第一条 CS 查询为连接及认证对照；第二条只将维度换为 TS。鉴权从本机生产配置读取，不在报告中重复完整 Token。

  ```text
  GET https://factor-backend.questvector.ai/api/v1/factor-ic/summary-metrics?is_sub_factor_id=true&ic_scope=cross_sectional&limit=1
  GET https://factor-backend.questvector.ai/api/v1/factor-ic/summary-metrics?is_sub_factor_id=true&ic_scope=time_series&limit=1
  ```

- **预期 / 实际**：预期合法最小查询返回一条对应维度的结果或有明确语义的正常空页。实际 CS 对照 `HTTP 200`，`11.509` 秒返回一条真实结果，summary `4355073`、`sub_factor:2109022`；随后的 TS 在 `2026-09-10T16:52:22.230406+00:00` 开始，`60.010` 秒后触发 `requests.exceptions.ReadTimeout`，没有收到 HTTP 响应。此处不能写成服务端返回 HTTP 500 或 `QUERY_TIMEOUT`；未收到服务端 request_id。
- **正式回归证据**：`test_backend_summary_identity_values_and_period_instants_match_mcp_database[time_series]` 同请求按项目配置共尝试三次，分别 `60.337 / 60.904 / 60.422` 秒超时，停在第一步 Backend 查询，未进行 DB 样本读取或 MCP 对账。同轮 `[cross_sectional]` 已通过原有同一 summary 的 Backend/MCP/DB 身份、数值和显式 period 时间断言。两节点原始结果保持 `1 PASSED / 1 FAILED`，不将 TS 改成通过。
- **正式复现入口**：`python3 -m pytest 'tests/cases/factor4/test_backend_three_way_business.py::test_backend_summary_identity_values_and_period_instants_match_mcp_database[time_series]' --live --env production -v --tb=short`。命令按现有配置可能自动重试；无重试最小请求及 CS 对照单独保存在诊断文件中。
- **证据**：[原始 pytest 输出](../reports/factor4-production-backend-summary-final-20260910.log)、[两节点 JUnit](../reports/factor4-production-backend-summary-final-20260910.xml)、[无重试请求与同连接 CS 对照](../reports/factor4-production-backend-summary-diagnostic-20260910.json)、[实时 OpenAPI 契约和取样修正](../reports/factor4-production-backend-summary-contract-20260910.json)。
- **结论边界**：本次两条用例已改用公开返回结果定位 DB，移除了“因子全历史不超过 20 条”的本地取样前置，原有数据断言保留。当前 TS 失败发生在公开查询本身，不能归为本地样本发现失败，也不能推断因子数值错误。它与 MCP 的“TS 指标范围查询超时，使用端链路无法开始”属于不同端点和请求，目前没有证据确认两者内部根因相同。

## 2026-09-07 结果级复跑固定名称

| 固定中文标题 | 本轮裁决 |
| --- | --- |
| 公式绑定缺证据 | 正式 batch 6 的 32 个已发布子因子没有运行公式证据；更广的 metric-to-run 强链接也缺失，不等同于公式算错 |
| 环境快照缺失日期与当时可用环境记录不一致 | 仍为 CANDIDATE；daily 1341 的同一日期差异复现，尚无已确认数值影响 |
| `new` 状态子因子目录分页验收失败 | 2026-09-08 契约澄清：预算终止属于预期保护，不计产品 Bug；原失败归属测试判定器，修正记录见下方 |
| 成本结果证据不完整 | QA batch 7 的人工样本缺字段，不是已证实成本计算错误 |
| 因子定义版本证据不完整 | QA batch 7 缺独立 definition 版本，不是已证实正式版本混用 |
| OOS 时间证据不完整 | QA batch 7 缺 folds，不是已证实未来数据泄漏 |
| 推荐结果回查误报指标快照变化 | 测试判定器比较原列与 JSON 投影导致差异，不是实际快照漂移 |

2026-09-07 当时样本回归通过的历史标题：Backend 环境日期精确过滤遗漏已有环境记录；
Backend 指标周期时间戳时区转换错误（整体偏移 8 小时）；发布摘要路由数量与实际有效路由数量不一致。
历史证据及未纳入本轮的专项状态继续保留，不按本次通过推断所有历史问题均已修复。

2026-09-08 本地目录修正及去重后的 31 项定向回归：29 passed、2 failed，目录相关 24 项全部通过。
“发布摘要路由数量与实际有效路由数量不一致”再次出现，本次摘要 86、实际有效 route 83。
另一失败固定名称为“有效路由排名不连续”，其与摘要数量差异是否同一根因尚未确定，先作为候选保留，不重复增加独立根因计数。
详情见 [目录修正与用例去重](factor4-catalog-case-cleanup-20260908.md)，不是全量历史 Bug 复测。

2026-09-08 后续全量已执行全部 668 实例：467 passed、76 failed、125 skipped、0 errors，三个专项均开启。
核心结果级 478 中 373 passed、5 failed、100 skipped。76 为实例数，不是独立 Bug 数；
详细归并、公式元数据定向复现及历史边界观察见 [本次全量结果](factor4-full-live-20260908.md)。
本次没有修改用例来改变结果，旧历史总数不自动替换为本轮独立根因数。

### `new` 状态子因子目录分页验收失败

- **最新裁决**：2026-09-08，`NOT_A_PRODUCT_BUG`；固定中文标题保留作为原误报的追踪名称。
- **规则依据**：用户补充防批量导出用途及每分钟 2000 条限制；[MCP 使用说明第 3.2、5.6 节](https://jjp1ynw9z1yy.jp.larksuite.com/wiki/AdOhwoJLMiII4HkCSfJjUB2RpXg)明确单链到达预算后终止 cursor 并返回 warning。文档默认值可由部署配置覆盖。
- **事实边界**：9 月 7 日同链返回 2000 条后明确 `CATALOG_CURSOR_BUDGET_REACHED`，不是静默丢失其余 1420 个已有记录。该现象没有验证分钟限流或一分钟后的恢复机制，不混同日预算、分钟限流和单链累计量。
- **测试修正**：目录受限终态只核验已返回数据；自然末页才要求完整集合。无说明的提前终止、重复及错误数据仍失败，报告保留受限范围。
- **进度及证据**：测试判定器已修正，41 个离线实例通过，14 个目录 live 实例通过；其中本项返回 2000/3421 个因子，按受限范围验收，其余 13 个自然完结并对账完整集合。详见 [现有 Case 修正记录](factor4-existing-case-corrections.md) 及 `reports/factor4-catalog-bounded-live-20260908.xml`。原全量 JUnit 的 6 failed 保持历史原貌，不事后改写。

## 命名规则

1. **固定中文标题**是对外唯一 Bug 名称。问题再次出现、回归测试或验收复测时必须逐字使用同一个标题。
2. 英文索引（例如 `F4-ENV-BACKEND-EXACT-FILTER`）只用于内部检索，不替代固定中文标题。
3. Case ID（例如 `ENV-108`）只表示测试场景，不替代固定中文标题。一个根因覆盖多个 Case 时只登记一个 Bug。
4. 历史中文名称、旧 Case 名和脚本名作为别名保留；修复后只更新 `状态`、`最后验证` 和证据，不重命名固定中文标题。
5. `CONFIRMED` 才计入当前产品 Bug；`CANDIDATE`、`BLOCKED`、`DEFERRED` 和 `CLOSED` 不计入确认数量。
6. 本轮的 pytest 框架失败和测试账号前置失败不自动归因于 Factor 4.0 产品；它们在文末单独登记。

## 当前数量

| 口径 | 数量 | 说明 |
|---|---:|---|
| 正式 100-case 独立产品 Bug | 5 | 5 个 P1 根因；`DB-605` 是汇总 Case，不另计 |
| 历史论文/运行时专项确认问题 | 2 | 不计入正式 100-case 统计，但仍是待处理问题 |
| 当前确认问题合计 | 7 | 下表 `CONFIRMED` 的产品问题 |
| 待契约确认候选 | 4 | 不计 Bug 数量，避免把测试预期或未定契约误报为产品缺陷 |

## 固定中文标题总表

以下标题是后续测试报告、验收单和回归记录的唯一对外名称。英文索引和 Case 仅作定位。

| 固定中文标题 | 英文索引 | 关联 Case |
|---|---|---|
| Backend 环境日期精确过滤遗漏已有环境记录 | `F4-ENV-BACKEND-EXACT-FILTER` | `ENV-108`, `DB-605` |
| Backend 指标周期时间戳时区转换错误（整体偏移 8 小时） | `F4-METRIC-PERIOD-TZ` | `MET-310`, `DB-605` |
| DPO 公式错误地位移均线而非价格序列 | `F4-DPO-FORMULA` | `CALC-510` |
| 固定周期因子公式未应用声明窗口 | `F4-FIXED-HORIZON-FORMULA` | `CALC-510` |
| 发布摘要路由数量与实际有效路由数量不一致 | `F4-PUBLISHED-ROUTE-COUNT` | `DB-613` |
| 有效路由排名不连续 | `F4-ELIGIBLE-ROUTE-RANK-GAP` | `RESULT-504`，最终排名结果级检查 |
| 论文候选与注册因子语义映射错误 | `F4-KB-MAPPING-SEMANTIC-MISMATCH` | 论文/KB 专项 |
| IV/RV 因子定义与实际执行公式及输入字段不一致 | `F4-IV-RV-DEFINITION-RUNTIME-MISMATCH` | IV/RV 专项 |
| 公式已更新但 normalized_formula 元数据仍保留旧表达式 | `F4-NORMALIZED-FORMULA-STALE` | `CALC-510-A`，当前元数据专项 |
| 环境快照缺失日期与当时可用环境记录不一致 | `F4-FROZEN-MISSING-DATE-VISIBLE` | 环境全区间历史核验 |

### 环境快照缺失日期与当时可用环境记录不一致

- **英文索引**：`F4-FROZEN-MISSING-DATE-VISIBLE`
- **当前状态**：`CANDIDATE`，2026-09-07。按可见 ready 环境日期应进入快照的规则，用例失败；额外筛选契约尚待确认，不提前计为确认产品 Bug。
- **已确认事实**：批次 `6`、`7` 的冻结区间为 `2024-09-02` 至 `2026-09-01`，`missing_dates` 明确包含 `2024-09-02`。同一测试库日历记录 `1341` 对应该日 `fact / CHOPPY_UP / ready / revision=1`；`available_at=2026-08-31 15:43:44.121786`、`created_at=2026-08-31 15:43:50.999883`，均早于批次 6 的冻结时点。批次 7 复用了相同快照，不能算第二次独立计算复现。
- **时间口径**：批次列 `as_of_time=2026-09-02 01:17:17.390102`，冻结 JSON 为 `2026-09-01T17:17:17.390102Z`。这两种表示尚未作契约确认；本条日历记录早于两者超过一天，不能以一次常见的 8 小时时区转换消除此事实差异。
- **预期 / 实际**：若缺失定义为 as_of 时没有可用 ready 日历，则该日不应列为缺失；实际已声明缺失。没有证据证明这次差异已经改变因子数值或最终排名。
- **复现命令**：

  ```bash
  python3 -m pytest tests/cases/factor4/test_environment_closure_business.py::test_frozen_missing_dates_match_full_range_daily_history_at_batch_as_of --live --env test -v --tb=short
  ```

- **验证步骤**：在同一只读事务读取批次的冻结日期区间、as_of、members、missing_dates；独立查询该完整日期区间内同 label_kind 的所有 daily 修订，不按成员 ID、is_current 或 available_at 预过滤；按 as_of 选可见的最高修订，核对 ready 日期是否被列为 missing；另查创建时间排除事后回填。
- **证据**：`reports/factor4-closure-range-history-final-live-20260907.xml`。失败码为 `ENV_FROZEN_MISSING_DATE_HAS_VISIBLE_ENVIRONMENT`。
- **根因边界**：不能断定是开始日期边界或时区代码错误；当前公开日期字段未说明额外选择条件。`2026-08-30` 的 latest 日历为 not_ready，该日只记录选择规则不明，不当作同类失败。

### 公式已更新但 normalized_formula 元数据仍保留旧表达式

- **英文索引**：`F4-NORMALIZED-FORMULA-STALE`
- **当前状态**：`CONFIRMED`，2026-09-08 全量静态检查及真实 MCP 批量详情定向复核，确认公式输出元数据互相矛盾；不证明当前计算数值错误。
- **本轮事实**：`sub_factor:161104/161106/161108` 的 executable 详情 `calc_logic=mean(close, window) - close.shift(window // 2 + 1)`，`metadata.normalized_formula=-(close - mean(close, (60 + 0)).shift(((60 + 0)) // 2 + 1))`。最新 DB detail 不含 normalized 字段，MCP 仍返回旧元数据。复现调用 `factor_get_details_batch`，`factor_refs` 为上述三个，`detail_level=executable`；详见 [全量结果](factor4-full-live-20260908.md)。
- **历史事实**：2026-09-05 保存的报告中，`sub_factor:161104/161106/161108` 的 `calc_logic` 和精确公式 evidence 已使用正确 DPO，但 `metadata.normalized_formula` 仍保留位移均线的表达式。
- **根因边界**：这是元数据一致性问题，不能沿用「DPO 公式错误地位移均线而非价格序列」来断言实际运行错误。归一化前后仅周期参数不同且没有实际输入 cadence/单位转换证据时，只记录 `BLOCKED_DOC`；不得直接判为错误或通过。
- **测试判定修正**：家族回归只读取可执行 detail 与精确 Run evidence。当前详情静态审计独立核验归一化元数据，同层输入字段分别对账，禁止把逻辑输入与 raw dependency closure 合并后比较。
- **历史证据**：`reports/factor4-formula-regression/20260905T034346Z/adjudicated-results.json`、`reports/factor4-deep/20260905T110208Z-dpo-formula-recheck/report.json`。

## 确认的产品 Bug

### Backend 环境日期精确过滤遗漏已有环境记录

- **英文索引**：`F4-ENV-BACKEND-EXACT-FILTER`
- **历史别名 / Case**：`ENV-108`；“Backend 精确 environment_date 过滤漏行”；`DB-605`（汇总）
- **状态 / 严重度**：`CONFIRMED` / `P1`
- **归属**：Backend 环境查询；不是 MCP 传输或协议问题。
- **影响**：调用方按文档传入精确 `environment_date` 时，Backend 返回空集合或漏行；同条件 MCP 和数据库仍能命中当前记录，造成三方结果不一致。
- **已确认事实**：`fact` 日期 `2026-09-02` 应命中 `id=2182`；`forecast` 日期 `2026-09-03` 应命中 `id=2183`。Backend 带精确日期返回 `[]`，不带日期可以返回记录；带不带 `as_of` 均复现。
- **复现命令**：

  ```bash
  python3 -m pytest tests/cases/factor4/test_backend_three_way_business.py::test_backend_daily_exact_date_and_pit_match_mcp_database --live --env test -v
  ```

- **核心请求**：

  ```text
  GET /api/v1/market-environments/daily?label_kind=fact&environment_date=2026-09-02&include_revisions=false&limit=10
  ```

- **预期 / 实际**：预期 Backend 返回包含 `id=2182` 的 fact；实际返回 `[]`。forecast 用 `2026-09-03` 同样预期 `id=2183`，实际漏行。
- **最新证据**：`reports/factor4-resume/20260904T071013Z-calc508-env108-met310/adjudicated-summary.json`。
- **根因边界**：已确认 exact-filter 行为错误；具体部署代码中的 SQL/时区根因尚未直接验证。

### Backend 指标周期时间戳时区转换错误（整体偏移 8 小时）

- **英文索引**：`F4-METRIC-PERIOD-TZ`
- **历史别名 / Case**：`MET-310`；“Backend 指标 period 时间比 DB/MCP 早 8 小时”；`DB-605`（汇总）
- **状态 / 严重度**：`CONFIRMED` / `P1`
- **归属**：Backend 时间序列化；不是 MCP 协议问题。
- **影响**：同一指标的身份、数值和 run 可以一致，但 Backend 的 `period_start/period_end` 表示了错误的瞬时时点，调用方按时间窗口回放或对账会发生边界偏移。
- **已确认事实**：因子 `sub_factor:1325857` 的 cross-sectional 指标 `982821` 和 time-series 指标 `982822`，数据库 `metrics_json` 使用 UTC `2026-07-25T00:00:00Z`、`2026-07-26T00:00:00Z`；MCP 正确显示为 `2026-07-25T08:00:00+08:00`、`2026-07-26T08:00:00+08:00`；Backend 显示为 `2026-07-25T00:00:00+08:00`、`2026-07-26T00:00:00+08:00`，早 8 小时。
- **复现命令**：

  ```bash
  python3 -m pytest tests/cases/factor4/test_backend_three_way_business.py::test_backend_summary_identity_values_and_period_instants_match_mcp_database --live --env test -v
  ```

- **复现步骤**：对同一 `factor_ref`、metric ID 和 run，分别读取 Backend、MCP 和数据库 `metrics_json` 的 `period_start/period_end`，先统一为 UTC 再比较；Backend 两个端点各出现 `-08:00` 偏移。
- **最新证据**：`reports/factor4-resume/20260904T071013Z-calc508-env108-met310/adjudicated-summary.json`。
- **根因边界**：已确认序列化结果不一致；“naive datetime 被本地时区化”只是合理推测，未直接验证部署代码。

### DPO 公式错误地位移均线而非价格序列

- **英文索引**：`F4-DPO-FORMULA`
- **历史别名 / Case**：`CALC-510-DPO`；`CALC-510` 中的 DPO 公式问题；DPO 论文因子窗口问题
- **状态 / 严重度**：`CONFIRMED` / `P1`
- **归属**：持久化因子定义/计算公式；不是 MCP 传输问题。MCP、DB 和 active route 反而一致地暴露了错误公式。
- **影响因子**：`sub_factor:161104`（detail `169779`）、`sub_factor:161106`（detail `169781`）、`sub_factor:161108`（detail `169783`）。
- **已确认事实**：当前持久化公式为 `-(close - mean(close, 60).shift(31))`，等价于移动均线先位移；独立 DPO oracle 为 `SMA(close, 60) - close.shift(31)`。合成序列在 warmup 后 `150/150` 个点不一致，index 150 实际约 `-834.6896`、oracle 约 `39.1515`。
- **复现命令**：

  ```bash
  python3 -m pytest tests/cases/factor4/test_formula_catalog_business.py -k dpo --live --env test -v
  ```

- **复现步骤**：读取每个因子的 detail、immutable formula evidence 和最新 completed run；用独立序列 `close[i] = 100 + i^2/17 + 0.13*(i mod 7)` 重算两种公式并比较 warmup 后输出。
- **最新证据**：`reports/factor4-deep/20260904T071236Z-dpo-formula-recheck/report.json`。
- **边界**：本 Bug 不包含历史 VWAP 观察项；VWAP 另按 `DEFERRED` 记录。
- **2026-09-10 生产定向复核**：`CONFIRMED`。本次精确核对的生产样本是 `sub_factor:161106`，名称为 `dpo_mean_reversion_tf_1h_n_60`，声明窗口 `n=60`；未把测试环境另两个历史 ID 的结论自动外推到生产。生产 MCP 与最新 DB detail `169781` 的公式一致，detail 的 `updated_at` 原值为 `2026-09-10 07:03:27`。
- **生产实际字段**：`formula_summary="factor = -DPO(close,n)"`；`calc_logic="-(close - mean(close, (60 + 0)).shift(((60 + 0)) // 2 + 1))"`；`params.fields=["close"]`；`params.window=60`。当前可执行表达式仍对均线位移 31 bars；按既有 DPO 验收定义应为 `mean(close, 60) - close.shift(31)`。
- **生产 MCP 复现参数**：完成 initialize 后，向生产 `/mcp/factor-data` 发送以下 `tools/call`；使用独立生产配置鉴权，不在报告中写入 Token。

  ```json
  {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
      "name": "factor_get_details_batch",
      "arguments": {
        "factor_refs": ["sub_factor:161106"],
        "detail_level": "executable"
      }
    }
  }
  ```

- **生产验证步骤及结论边界**：本次实际在同一个 4 因子请求中查询该因子及下方 3 个 IV/RV 因子，返回 `HTTP 200`、`isError=false`。检查 `data.items[].data` 的名称、摘要、`calc_logic` 和输入字段，并与 `sub_factors` 定义及按 `updated_at DESC,id DESC` 选出的最新 `factors_details` 对账，即可确认当前公开公式错误；不是仅 `normalized_formula` 元数据陈旧。R0 正式失败见 `reports/factor4-production-remaining-20260910.xml`。当前发布 metric 与 immutable formula 的强绑定证据不足，本次不能据此断言已发布数值、score 或 rank 使用了错误公式，也不以历史合成序列数值代替生产数值验收。

### 固定周期因子公式未应用声明窗口

- **英文索引**：`F4-FIXED-HORIZON-FORMULA`
- **历史别名 / Case**：`CALC-510-FIXED-HORIZON`；`CALC-510` 中的固定周期窗口公式问题
- **状态 / 严重度**：`CONFIRMED` / `P1`
- **归属**：因子公式生成/持久化；不是 MCP 传输问题。
- **影响因子**：`sub_factor:181`（detail `325`，声明 48h）、`sub_factor:183`（detail `327`，声明 72h）、`sub_factor:274`（detail `418`，声明 48h）、`sub_factor:276`（detail `420`，声明 72h）。
- **已确认事实**：
  - `181/183` 当前均为 `funding_rate.diff(12).diff(12)`，原始依赖跨度 24 bars；按家族语义分别应进入 `diff(24).diff(24)` 和 `diff(36).diff(36)`。
  - `274/276` 当前均为 `long_short_ratio.pct_change(24)`，原始依赖跨度 24 bars；按声明窗口分别应为 `pct_change(48)` 和 `pct_change(72)`。
  - 控制因子 `sub_factor:180`（24h）通过，说明问题集中在非 24h 变体的窗口参数未进入公式语义。
- **复现命令**：

  ```bash
  python3 -m pytest tests/cases/factor4/test_formula_catalog_business.py::test_fixed_horizon_declared_and_completed_formula_candidates --live --env test -v
  ```

- **复现步骤**：读取 detail 声明窗口、immutable formula evidence 和最新 completed run 的原始依赖 offsets；比较声明 bars 与最大依赖跨度，并按同一公式家族生成独立 oracle。
- **最新证据**：`reports/factor4-resume/20260904T150933+0800-fixed-horizon-adjudication/summary.md`。
- **边界**：这是一个公式族问题，四个因子共用同一个固定中文标题；不要拆成四个重复 Bug。

### 发布摘要路由数量与实际有效路由数量不一致

- **英文索引**：`F4-PUBLISHED-ROUTE-COUNT`
- **历史别名 / Case**：`DB-613`；“published environment_status.route_count 与 active route 实际数量不一致”
- **状态 / 严重度**：`CONFIRMED` / `P1`
- **归属**：数据库发布摘要聚合/写入；没有 MCP/HTTP 调用，不是 MCP 问题。
- **已确认事实**：同一只读事务内连续读取 published success batch `6`、同一 publication/version 和 `WIDE_RANGE`：摘要 `environment_status.route_count=0`；按相同身份条件精确统计 `active + eligible` route 为 `86`；第二次读取仍为 `0/86`，快照稳定且 route 身份错配为 `0`。
- **复现命令**：

  ```bash
  python3 -m pytest tests/cases/factor4/test_environment_closure_business.py::test_each_label_metrics_routes_and_summary_share_full_publication_identity --live --env test -v
  ```

- **复现步骤**：在 `START TRANSACTION READ ONLY` 中读取 batch 的 `environment_status.WIDE_RANGE.route_count`；再用同一 `batch/publication_uid/publish_version/market_scope/label_code` 查询 `market_environment_factor_route` 的 active eligible 数量；重复读取一次并回滚。
- **预期 / 实际**：预期摘要数量等于精确 route 数量；实际 `0 != 86`。
- **最新证据**：2026-09-07 新增闭环用例真实只读复核，`reports/factor4-closure-final-live-20260907.xml`；同名问题仍复现 `WIDE_RANGE route_count=0`、实际 active eligible route 为 `86`。本次入口为 `test_environment_closure_business.py::test_each_label_metrics_routes_and_summary_share_full_publication_identity[WIDE_RANGE]`，其余五种 label 的该项对账通过；这不是全量历史 Bug 重新验收。历史证据继续保留在 `reports/factor4-resume/20260904T151150+0800-db613-targeted-closure/adjudicated-summary.json`。
- **2026-09-08 定向复核**：`reports/factor4-catalog-dedup-live-20260908.xml`，同一中文标题再次失败：`WIDE_RANGE` 摘要 `86`，实际 active eligible `83`。9 月 7 日后续样本曾通过的记录保持原貌，不据此覆盖本次差异。
- **边界**：不要把 `DB-605` 单独登记；它只是 `F4-ENV-BACKEND-EXACT-FILTER` 与 `F4-METRIC-PERIOD-TZ` 的汇总失败。

### 有效路由排名不连续

- **英文索引**：`F4-ELIGIBLE-ROUTE-RANK-GAP`
- **状态**：`CANDIDATE`，2026-09-08。有效结果排名不连续已证实；与上述摘要数量差异是否同一根因尚未确定，不先计作第二个独立产品根因。
- **实际结果**：测试库 batch `6`、`all/default`、`WIDE_RANGE`、环境日期 `2026-09-01`，active eligible route 共 `83` 条，rank 范围 `1..86`，缺少 `74、75、84`，没有重复 rank。正式用例的初次与重复读取均报 `RANK_NOT_CONTIGUOUS`，没有 publication 切换或重复读取漂移。
- **预期**：按现有结果级验收规则，同分区的 active eligible 排名应从 1 连续至实际结果数；不据此推断评分计算错误。
- **复现命令**：

  ```bash
  python3 -m pytest tests/cases/factor4/test_final_results.py::test_final_result_ranking_is_partitioned_and_repeatable --live --env test -v --tb=short
  ```

- **验证步骤**：使用测试配置读取当前 active published 分区；按完整 publication/batch/profile/label/环境日期身份筛选 active eligible route；按 rank 排序，与 `1..N` 比较；再次读取同 publication 检查是否稳定。当前样本预期为 `1..83`，实际仍含 `85、86` 且中间缺位。
- **证据**：`reports/factor4-catalog-dedup-live-20260908.xml`。另经只读 Repository 的 `read_published_route_snapshot("all", "default")` 核对当前 83 条及上述缺位，仅输出数字/分区摘要，不读取原始计算过程、不写库。
- **处理边界**：保留用例；不因本次清理而删除失败项，不把两次读取的两个失败码计成两个 Bug。

### 论文候选与注册因子语义映射错误

- **英文索引**：`F4-KB-MAPPING-SEMANTIC-MISMATCH`
- **历史别名 / 专项**：论文/KB 映射语义错误；KB 五样本映射错误
- **状态 / 严重度**：`CONFIRMED` / `P1`（历史专项，不计入正式 100-case）
- **归属**：论文候选到因子结果的映射/调度数据链路；不是 MCP 传输问题。MCP 查询均为 HTTP 200，问题在返回对象语义。
- **已确认事实**：以下 5 对对象均返回 `mapping_status=mapped`、`result_validity=valid`，且 detail 查询 HTTP 200，但候选字段、频率或经济含义与实际子因子不一致：

  | extraction | result sub-factor | 候选语义 | 实际因子语义/字段 |
  |---:|---:|---|---|
  | `64014` | `1597753` | XRP/BTC ETF 周度流入流出背离 | `aggressive_flow_price_divergence_zscore_24h`；1h `close/taker_buy_volume/volume` |
  | `64009` | `1599721` | Polymarket 事件概率与临近时间 | `event_prob_amplitude_pressure_24h`；1h `long_short_ratio/high/low` |
  | `63998` | `1584138` | 多资产 TVP-VAR 最小关联度组合权重 | `connectedness_premium_corr_168h`；单资产 `close/premium_index` |
  | `64011` | `1592833` | LSTM 日频 7-30 天价格预测 | `lstm_proxy_volatility_sentiment_regime_24h`；1h `close/long_short_ratio` |
  | `64000` | `1585108` | PoW/ESG 多资产能源溢价篮子 | `crypto_energy_esg_vol_volume_zscore`；1h `close/volume` |

- **复现命令**：本轮直调报告已保存；若需要再次核对，可按报告中的 5 个 `kb_factor_candidate_search` 请求逐个发送，并用同一 `mapped_factor_id` 调 `factor_get_detail`。上一轮完整验收脚本的结果也保留在 `reports/factor4-deep/20260904T111452+0800-kb-ts-bug-acceptance-rerun/acceptance-summary.json`。
- **复现步骤**：对每个 extraction ID 做精确 KB 查询；读取返回的 `dependent_data_fields/data_frequency/holding_period`；再读取 mapped sub-factor detail 的 `formula_summary/factor_bar_interval/fields`；只要身份虽 mapped 但这些核心语义不一致，即复现。
- **最新证据**：`reports/factor4-deep/20260904T072026Z-kb-mapping-five-recheck-v2/mapping-summary.json`（5/5 MCP 查询成功，DB 只读事务回滚）。
- **边界**：`task_status=failed` 或 quota 阻断本身不另计 Bug；本记录只针对已存在的错误映射。

### IV/RV 因子定义与实际执行公式及输入字段不一致

- **英文索引**：`F4-IV-RV-DEFINITION-RUNTIME-MISMATCH`
- **历史别名 / 专项**：IV/RV 定义与执行公式不一致
- **状态 / 严重度**：`CONFIRMED` / `P1`（历史专项，不计入正式 100-case）
- **归属**：因子定义、可执行公式和数据源能力之间的契约/运行时链路；不是 MCP 传输问题。
- **影响因子**：`sub_factor:161628`（detail `170303`，30d）、`sub_factor:161629`（detail `170304`，7d）、`sub_factor:161630`（detail `170305`，90d）。
- **已确认事实**：定义声明 `ATM_IV - realized_vol`，但 immutable formula evidence、执行公式和当前输入字段仍是 `close.pct_change().rolling(window).std()` 一类 realized-vol 计算；`ATM_IV` 与 `realized_vol` 被列为缺失源字段，旧 active route/metric 仍存在。定义、运行时公式和可用字段不能同时满足论文/因子定义。
- **复现命令**：

  ```bash
  python3 -m pytest tests/cases/factor4/test_formula_catalog_business.py -k iv_rv --live --env test -v
  ```

- **复现步骤**：分别读取 detail summary、definition、executable formula、raw schema 和 metrics；核对定义字段集合与执行字段集合，并检查 active route 是否仍指向该定义。
- **最新证据**：`reports/factor4-deep/20260904T071317Z-iv-rv-definition-recheck/report.json`。
- **边界**：这是 3 个 tenor 变体的同一根因，不拆成 3 个 Bug；缺少真实 IV/RV 源数据是运行阻断的一部分，但不抹去定义与执行证据已经冲突的事实。
- **2026-09-10 生产定向复核**：`CONFIRMED`。本次核对生产名称与 ID 的对应关系：`sub_factor:161628` 为 `iv_rv_premium_tf_1h_tenor_30d`、`161629` 为 `iv_rv_premium_tf_1h_tenor_7d`、`161630` 为 `iv_rv_premium_tf_1h_tenor_90d`。最新 DB detail 分别为 `170303/170304/170305`，`updated_at` 原值依次为 `2026-09-10 07:29:18`、`2026-09-10 07:29:19`、`2026-09-10 07:29:12`；不是测试环境固定 ID 误配。
- **生产实际字段**：三者均返回 `formula_summary="factor = ATM_IV - realized_vol"`、`calc_logic="close.pct_change().rolling(window).std()"`、`params.fields=["close"]`；`params.window` 分别为 `tenor=30d`、`tenor=7d`、`tenor=90d`。生产 MCP executable 与最新 DB detail 完全一致。实际表达式只计算 close 收益率的滚动标准差，缺少 IV 输入及 IV−RV 运算，无法实现所声明的差值公式。
- **生产 MCP 复现参数**：完成 initialize 后，向生产 `/mcp/factor-data` 发送以下 `tools/call`，鉴权读取独立生产配置。

  ```json
  {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
      "name": "factor_get_details_batch",
      "arguments": {
        "factor_refs": ["sub_factor:161628", "sub_factor:161629", "sub_factor:161630"],
        "detail_level": "executable"
      }
    }
  }
  ```

- **生产验证步骤及结论边界**：本次实际与上方 DPO 合并为 4 因子有界请求，`HTTP 200`、`isError=false`，三个 IV/RV executable 均可读取。逐项检查 `data.items[].data` 的定义摘要、`calc_logic` 和 `params.fields`，再与同 ID 的数据库定义和最新详情对账。R0 正式公式失败见 `reports/factor4-production-remaining-20260910.xml`；该报告中 `161629/161630` 的 MCP projection missing 在本次定向复核中不复现，不另计产品 Bug。当前发布 metric 到公式证据的强链接不足，本次确认的是当前定义与公开执行公式矛盾；不能将未绑定的历史 evidence 当作当前批次执行证据，也不能推断已发布因子数值、score 或 rank 已受影响。

## 待契约确认的候选（不计 Bug）

这些记录保留固定名称，后续若产品契约确认为缺陷，直接把状态改为 `CONFIRMED`，不得重新命名。

### Top-up 因子公式与父主题公式不一致

- **英文索引**：`F4-TOPUP-FORMULA-FALLBACK-SEMANTICS`
- **状态**：`CANDIDATE`
- **现象**：active top-up 路由审计发现 145/192 个 top-up 公式与同父主题、同窗口的正式因子不同；部分使用 `close.pct_change().rolling(window).mean()` 等通用补足公式。
- **为什么暂不计 Bug**：当前产品契约没有确认 top-up 是否允许通用 fallback 公式替代父主题公式。仅凭公式不同不能证明实现错误。
- **证据**：`reports/factor4-deep/20260904T071552Z-kb-topup-route-audit/summary.md`。

### 嵌套组合因子是否允许的接口契约未确定

- **英文索引**：`QT-COMBO-NESTED-COMPOSITE-CONTRACT`
- **状态**：`CANDIDATE`
- **现象**：测试先登记一个组合因子，再把它作为新组合成员提交，Backend 返回 HTTP 422：`combination factors cannot be used in another factor combination`。
- **为什么暂不计 Bug**：现有测试名称预期允许嵌套，但服务端错误信息明确禁止嵌套；需要产品契约先决定哪一个是正确行为。
- **证据 / 用例**：`tests/cases/factor_combo/test_combo_scenarios.py::test_registered_composite_sub_factor_can_be_used_in_a_new_form`。

### 工作单 direction 字段正负值契约矛盾

- **英文索引**：`QT-WORK-ORDER-DIRECTION-CONTRACT`
- **状态**：`CANDIDATE`（更像测试框架契约矛盾）
- **现象**：真实母因子工作单返回成员 `direction=-1`；Service 的 `_required_response_int` 默认先要求 `direction >= 1`，而后续成员校验又要求 `direction in {-1, 1}`。
- **为什么暂不计 Bug**：需要先确认接口文档是否允许负方向。若允许，修复测试框架校验；若不允许，才登记 Backend 数据 Bug。
- **证据 / 用例**：`tests/cases/factor_combo/test_combo_scenarios.py::test_real_parent_factor_flow_reaches_a_classified_terminal_outcome`；`service/factor_combo_service.py`。

### Feedback action 字段未纳入测试框架映射

- **英文索引**：`QT-FEEDBACK-ACTION-MAPPING`
- **状态**：`CANDIDATE`（测试框架映射遗漏）
- **现象**：Feedback API 和数据库业务断言均通过，但响应额外返回 `action=continue_exploration`，当前 persistence allowlist 未映射该字段，测试以 `FAIL_CONTRACT` 结束。
- **为什么暂不计 Bug**：API 已明确返回且业务状态正确；当前证据更支持测试框架字段映射遗漏，不应直接归因后端。
- **证据 / 用例**：`tests/cases/factor_combo/test_feedback_api.py::test_feedback_rejects_combo_and_resets_form_for_next_round`；`service/factor_combo_persistence.py`。

## 测试环境/前置阻断（不计产品 Bug）

### 2026-09-08 全量执行的固定测试问题名称

| 固定中文标题 | 本轮失败实例 | 裁决 |
| --- | ---: | --- |
| 参数校验错误的文本响应被误判为 MCP 返回格式错误 | 58 | 本地 JSON-only 解析路径问题，代表请求已确认返回明确拒绝；未逐个改判为通过 |
| Accept 协商用例错误要求仅声明 SSE 的 POST 请求成功 | 1 | 本地请求和预期不符合 POST Accept 要求 |
| 协议版本协商用例错误限制服务端只能返回配置版本 | 1 | 服务端实际协商到另一受支持版本，非接受虚构版本 |
| 子因子分页重放用例将动态游标变化误判为业务数据变化 | 3 | 本地比较了动态 children_next_cursor，业务数据一致 |
| 批量指标查询对重复因子引用的返回规则未明确 | 1 | 公开去重/保留输入位置契约待确认，不计产品 Bug |
| 计算结果审计记录缺少 request_id | 4 | 技术审计观察，不据此认定计算结果错误 |

本轮证据和代表复核见 [全量结果](factor4-full-live-20260908.md)。此表不是修复记录；本轮没有修改这些用例。
“Slice 结束时间边界错误”本次显式全开执行后仍复现，保留下面历史暂不处理归类，不混入核心结果级 Bug 数量。

| 固定中文标题 | 英文索引 | 本轮表现 | 需要的处理 |
|---|---|---|---|
| restricted 测试账号权限配置不符合负向场景 | `QT-RESTRICTED-ACCOUNT-FIXTURE` | 2 个权限负向用例 setup error；restricted 账号实际拥有 `use_factor_agent`。 | 提供确实不含受测权限的测试账号，或明确调整权限负向契约。 |
| non-owner 测试账号被封禁导致所有权场景阻断 | `QT-NON-OWNER-ACCOUNT-FIXTURE` | 7 个所有权隔离用例 setup error；non-owner 登录 HTTP 403，返回“账号已封禁”。 | 提供已启用且具备业务权限、但不是资源所有者的账号。 |
| factor_reader_db 推荐依赖不可用 | `EXT-READER-DB` | 推荐正向/PIT 分支返回 `factor_reader_db/DEPENDENCY_UNAVAILABLE`。 | 恢复 reader DB 后重跑受阻分支。 |
| 生命周期写入测试门禁未开启 | `EXT-R1-HMAC-SCHEDULER` | 生命周期写入、故障注入和历史切换用例未开启写入门禁。 | 提供授权 JWT/HMAC、专用 fixture 和清理窗口。 |

## 已关闭或按用户要求暂缓的历史记录

以下名称保留用于历史追踪，但当前不计入开放 Bug。除非用户明确要求重新纳入，否则不要在“当前未修复 Bug”列表中重复报告。

| 固定中文标题 | 英文索引 | 历史名称/现象 | 当前状态 | 备注 |
|---|---|---|---|---|
| 币种级 time-series 排名完全不可用 | `F4-RANK-TS-SYMBOL-SCOPE` | “币种级 time-series 排名完全不可用” | `CLOSED` | 后续排名/validity 复验通过；旧失败来自错误的历史行 oracle。 |
| Parent 指标自动选择返回旧周期 | `F4-PARENT-METRIC-STALE-PERIOD` | “Parent 指标自动选择返回旧周期” | `CLOSED` | 最新 parent/ranking 快照专项未发现新的 FAIL。 |
| VWAP 实际为累计 VWAP 而非滚动窗口 | `F4-VWAP-CUMULATIVE-WINDOW` | “VWAP 实际为累计 VWAP，非滚动窗口” | `DEFERRED` | 按当前测试指令跳过；不得并入 DPO 或 fixed-horizon Bug。 |
| 孤儿记录 | `F4-ORPHAN-RECORD` | 孤儿记录 | `DEFERRED` | 用户明确暂不处理。 |
| Slice 结束时间边界错误 | `F4-SLICE-END-TIME-BOUNDARY` | 结束时间边界错误 | `DEFERRED` | 用户明确暂不处理。 |
| 引用不存在的文档 | `F4-UNAVAILABLE-DOCUMENT-REFERENCE` | 引用不存在的文档 | `DEFERRED` | 用户明确暂不处理。 |
| 体验、规范与兼容性观察 | `F4-EXPERIENCE-COMPATIBILITY` | 体验、文案、规范、兼容性观察 | `EXCLUDED` | 按测试口径去除，不作为功能 Bug。 |

## 本轮执行索引

### pytest 全量

```bash
python scripts/run_tests.py --env test --report /tmp/questtest-offline-20260904.xml
python scripts/run_tests.py --env test --live --report /tmp/questtest-live-20260904.xml
```

- 离线：`318 collected / 191 passed / 127 skipped / exit 0`。
- 测试环境 live：`318 collected / 306 passed / 3 failed / 9 errors / 0 skipped / exit 1`。
- 3 个失败对应上文 `QT-COMBO-NESTED-COMPOSITE-CONTRACT`、`QT-WORK-ORDER-DIRECTION-CONTRACT`、`QT-FEEDBACK-ACTION-MAPPING`；9 个 errors 是账号前置阻断，不计产品 Bug。

### Factor 4.0 专项

本轮 priority 专项均在测试环境执行；数据库检查使用只读事务并回滚，未执行生产写入：

100-case 汇总文件：`reports/factor4-resume/20260904T152617+0800-final-coverage/coverage.md`（`PASS 40 / FAIL 5 / BLOCKED 54 / EXCLUDED 1`）。

| 专项 | 结果/证据目录 |
|---|---|
| Protocol gap | `reports/factor4-resume/20260904T070313Z-protocol-gaps` |
| Tool matrix / PIT | `reports/factor4-resume/20260904T150434+0800-tool-matrix-pit` |
| Catalog boundary / KB remaining | `reports/factor4-resume/20260904T150559+0800-catalog-boundaries`、`reports/factor4-deep/20260904T151423+0800-catalog-kb-remaining` |
| Route integrity | `reports/factor4-resume/20260904T150646+0800-route-integrity-closure` |
| Temporal oracle | `reports/factor4-resume/20260904T150714+0800-temporal-oracle-closure` |
| Status/permission | `reports/factor4-resume/20260904T150814+0800-status-permission-closure` |
| Ranking/parent | `reports/factor4-resume/20260904T150850+0800-ranking-parent-snapshot-closure` |
| Fixed horizon | `reports/factor4-resume/20260904T150933+0800-fixed-horizon-adjudication` |
| DB-613 | `reports/factor4-resume/20260904T151150+0800-db613-targeted-closure` |
| DPO / IV-RV / KB | `reports/factor4-deep/20260904T071236Z-dpo-formula-recheck`、`reports/factor4-deep/20260904T071317Z-iv-rv-definition-recheck`、`reports/factor4-deep/20260904T072026Z-kb-mapping-five-recheck-v2` |

后续报告必须使用本文件的固定中文标题；英文索引只用于机器检索。状态变化示例：`DPO 公式错误地位移均线而非价格序列：CONFIRMED -> RESOLVED`；中文标题和英文索引均保持不变。
