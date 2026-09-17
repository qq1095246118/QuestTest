# Factor 4.0 生产全量回归结果 — 2026-09-10～11

## 结论

生产 MCP、Backend 和只读数据库已接入独立配置。本轮已执行全部 **171 个测试函数、691 个参数化实例，覆盖 30 个测试文件**，包括显式开启的计算内部、技术及历史暂缓专项。没有尚未调度的实例，但失败、前置错误和跳过不代表业务验收完成。

| pytest 原始结果 | 实例数 |
| --- | ---: |
| PASSED | 328 |
| FAILED | 117 |
| ERROR | 113 |
| SKIPPED | 133 |
| 合计 | 691 |

经逐节点核查，确认 **6 类业务 Bug**。它们涉及 22 个 FAILED 和 5 个因服务依赖超时而 SKIPPED 的实例；多个实例复现同一问题，不能按实例数计 Bug。组合公式用例也可能同时包含两个已确认问题。

最终逐节点证据：
- [合并 JUnit](../reports/factor4-production-complete-20260910.xml)
- [691 节点及原始状态、来源报告](../reports/factor4-production-complete-20260910.json)
- [逐节点人工归类与固定 Bug 中文标题](../reports/factor4-production-adjudication-20260910.json)

## 环境与执行方式

- MCP：`https://factor-frontend.questvector.ai/mcp/factor-data`。本轮初始化成功，生产自报 `factor-agent-data-service 0.2.0`，当前 Key 可见 23 个工具。
- Backend：`https://factor-backend.questvector.ai/api/v1`。用户提供的独立生产身份通过 `/me` 校验。
- 数据库：用户指定的生产 MySQL，库 `factor_db`；账号实际只有 SELECT、SHOW VIEW 权限，客户端及会话同时启用只读。
- 非敏感配置：[production.yaml](../config/production.yaml)。生产凭据在 Git 忽略的本机 `config/production.local.yaml`，权限为 0600；日志、报告及代码差异检查未发现完整生产 Token 或密码。
- 本轮只读取业务结果，不触发重算、发布、反馈提交或业务 DB 写入。

完整执行命令：

```bash
cd /Users/wrh/Downloads/QuestTest
python3 -u -m pytest tests/cases/factor4 --live --env production \
  --include-factor4-internal-calculation \
  --include-factor4-technical --include-factor4-deferred \
  -v --tb=short -rs -o junit_family=xunit1 \
  --junitxml=reports/factor4-production-regression.xml
```

本轮曾中断续跑，最终账本按精确 node ID 合并：保留 `full-r2` 的 49 个完整节点、`remaining` 首次中断节点之前的 41 个节点，以及 `final-remaining` 的全部 601 个节点。修正本地代码后的定向结果只替换原节点，不增加分母。合并工具核对全量 collection、日志终态及 XML，排除中断节点和空占位；未累加历次中断报告的总数。

各用例按自己的时点和范围对账，不宣称跨数小时的生产数据完全静止。合并脚本为 [merge](../reports/factor4-production-merge-20260910.py)，归类脚本为 [adjudicate](../reports/factor4-production-adjudicate-20260910.py)。

## 已确认业务 Bug 及复现步骤

下列 MCP 请求使用本机生产 Key，先完成正常 initialize / notifications/initialized，再调用指定工具。JSON 为完整 `tools/call` 参数，可放入 `{"jsonrpc":"2.0","id":"repro","method":"tools/call","params": ...}`。Backend 请求使用独立 Backend JWT，不能使用 MCP Key 代替。凭据不在本文重复。

### 1. Backend 推荐接口查询有效发布结果返回 HTTP 500

**问题说明：** 生产存在已发布且 active 的 `all/default` batch 7，但正常读取推荐结果返回内部错误，Backend 推荐入口无法使用。

复现步骤：
1. 使用有效 Backend JWT 调用以下 GET。
2. 查看 HTTP 状态和返回体。
3. 将 limit 改为 200，或添加合法 as_of，问题仍可复现。

```text
GET https://factor-backend.questvector.ai/api/v1/market-environment/recommendations?market_scope=all&route_profile_key=default&limit=1
Authorization: Bearer <生产 Backend JWT>
```

预期返回推荐或正常的业务空结果；实际 HTTP 500，`success=false`，提示“操作失败，请稍后重试”。无 as_of 的最小请求 request_id 为 `54c2c6c7-1716-4603-a99b-a3468e2ffc85`。

正式入口：`test_backend_three_way_business.py::test_backend_recommendations_publication_order_and_scores_match_mcp_database`。证据：[定向日志](../reports/factor4-production-backend-diagnostic-rerun-20260910.log)。当前 MCP 推荐入口能正常返回业务空结果；Backend 同类正常读取返回 HTTP 500，不能把该故障扩大为两个入口都失败。

### 2. DPO 公式错误地位移均线而非价格序列

**问题说明：** `sub_factor:161106`（`dpo_mean_reversion_tf_1h_n_60`）的当前可执行定义仍把均线向后移动，和本项目的负 DPO 定义不一致。

复现步骤：
1. 调用以下工具。
2. 查看 executable 中 calc_logic，并与最新 DB detail 169781 比较。
3. 对比被 shift 的对象。

```json
{
  "name": "factor_get_details_batch",
  "arguments": {
    "factor_refs": ["sub_factor:161106"],
    "detail_level": "executable"
  }
}
```

实际为 `-(close - mean(close, (60 + 0)).shift(((60 + 0)) // 2 + 1))`，移动的是均线；按本项目约定应为 `mean(close, 60) - close.shift(31)`，移动价格序列。

确认范围仅 161106。161104、161108 返回抽象 helper `-DPO(close,n)`，没有独立实现证据，不能追加为相同已确认样本。当前发布 metric 缺少强公式版本链接，因此本条确认定义错误，不外推所有已发布分数或数值已经算错。证据及原固定标题保存在 [Bug Registry](factor4-bug-registry.md)，helper 区分见 [定向证据](../reports/factor4-production-dpo-helper-diag-20260910.json)。

### 3. IV/RV 因子定义与实际执行公式及输入字段不一致

**问题说明：** 三个期限的因子声明计算隐含波动率与实现波动率之差，实际公式只计算 close 收益率的滚动标准差，缺少 IV 输入和减法。

复现步骤：
1. 调用以下工具，分别查看三个返回项。
2. 对比 formula_summary、calc_logic 和 params.fields。
3. 核对声明中的 IV 是否出现在实际输入及运算中。

```json
{
  "name": "factor_get_details_batch",
  "arguments": {
    "factor_refs": ["sub_factor:161628", "sub_factor:161629", "sub_factor:161630"],
    "detail_level": "executable"
  }
}
```

预期实现声明的 `ATM_IV - realized_vol`；实际逻辑为 `close.pct_change().rolling(window).std()`，输入只有 `["close"]`。对应期限分别为 30d、7d、90d，合并为一个问题。

MCP 与 DB 三层详情一致、Run 身份关联通过，不改变公式内部缺少 IV 的事实。本条同样不推断未绑定的已发布数值和排名。正式入口：`test_formula_catalog_business.py::test_formula_detail_levels_internal_semantics` 的三个 ID 参数。

### 4. 环境指标最小分页查询仍超过响应大小限制

**问题说明：** 指定单因子、单批次、单环境和单维度，limit 降到 1 仍无法读取已有有效指标；工具没有字段裁剪参数可继续缩小响应。

复现步骤：
1. 调用以下最小工具请求。
2. 查看工具错误码。
3. 在 DB 核对该条件对应的唯一指标 2381，状态为 success、is_valid=1。

```json
{
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
```

实际 HTTP 200、`isError=true`、`RESPONSE_TOO_LARGE`，上限 2,621,440 字节，没有返回指标。DB 单行 metric_payload 为 38,796 字节；响应膨胀发生在哪里尚未定位。

同一因子的 `factor_get_environment_tags({"factor_ref":"sub_factor:149","market_scope":"all","route_profile_key":"default"})` 也受限，该工具没有分页参数。将其记录为同一问题的另一影响点，不宣称已证明两个端点的内部根因相同。证据：[最小请求及 DB/Schema 核对](../reports/factor4-production-selector-response-size-20260910.json)。

### 5. TS 指标范围查询超时，使用端链路无法开始

**问题说明：** 使用端先获取 TS 可用指标范围；合法请求即使 limit=1 也触发服务端 reader_db 查询超时，后续搜索、排名和结果回查无法从这个公开入口开始。

复现步骤：
1. 调用以下工具。
2. 等待服务返回错误，核对 timeout_seconds 和 dependency。
3. 将 limit 改为 100，再次核对。

```json
{
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
```

实际约 20.205 秒返回 HTTP 200、`isError=true`、`QUERY_TIMEOUT`，`dependency=reader_db`、`timeout_seconds=20`。limit=100 约 20.260 秒同样失败。

最小请求 request_id 为 `8d47d543-d0ae-49b0-9d39-4a8d2780817a`。独立 DB 小范围查询已取得 TS 币种及聚合范围，不能解释成生产无 TS 数据。四个 TS consumer 节点原状态为 SKIP，但人工归类为已确认服务阻塞。证据：[定向日志](../reports/factor4-production-consumer-ts-rerun-20260910.log)。

### 6. Backend TS 指标最小查询超时

**问题说明：** Backend 请求一条子因子 TS 汇总指标，连续不能在客户端 60 秒时限内返回；没有进入后续 DB/MCP 数据比对。

复现步骤：
1. 同一 Backend JWT 下先调用 CS 对照请求。
2. 仅将 ic_scope 改为 time_series，limit 仍为 1，单次读取超时设为 60 秒。
3. 关闭重试观察单次结果；项目正式用例按配置会重复尝试。

```text
GET https://factor-backend.questvector.ai/api/v1/factor-ic/summary-metrics?is_sub_factor_id=true&ic_scope=cross_sectional&limit=1
GET https://factor-backend.questvector.ai/api/v1/factor-ic/summary-metrics?is_sub_factor_id=true&ic_scope=time_series&limit=1
Authorization: Bearer <生产 Backend JWT>
```

同客户端 CS 约 11.509 秒返回 HTTP 200；TS 约 60.010 秒报 `requests.exceptions.ReadTimeout`。没有收到 HTTP 响应或服务端错误码，不能写成 HTTP 500 或服务端 QUERY_TIMEOUT。未证明与 MCP TS scope 查询有同一根因。

正式入口：`test_backend_three_way_business.py::test_backend_summary_identity_values_and_period_instants_match_mcp_database[time_series]`。证据：[最小对照请求](../reports/factor4-production-backend-summary-diagnostic-20260910.json)、[正式两节点报告](../reports/factor4-production-backend-summary-final-20260910.xml)。

## 已通过的主要业务范围

以下都是本轮真实线上结果，不含离线 unit 通过数：

- 目录扩展组 38 个实例通过；母因子目录分页、统计和现有目录限制规则按各用例的实际范围核对。
- MCP 当前推荐 limit=1/20/200 的请求与当时预期对账通过；已有定向样本为 NARROW_RANGE / NO_ELIGIBLE_FACTOR 空结果，不能据此宣称已验证非空推荐列表的分数和排序。数据库发布摘要、active eligible route 数量及排名连续性另有独立通过结果。
- 当前 batch 7 为明确的 per_factor_incremental / partial_fail / published / active，CHOPPY_UP 21 条、WIDE_RANGE 74 条有效路由；其余四类没有有效维度候选，不能只因路由为 0 报 Bug。两类有路由环境的完整候选准入证明仍缺冻结配置，未宣称全闭环通过。
- 六类环境冻结日期、分段、样本量，以及 OOS 最终时间范围通过；文档 V1 分数的独立 Decimal 重算与 TS/CS 准入规则通过。缺少自然边界样本的分支另列缺口。
- CS consumer 的搜索和排名两条完整六阶段链路通过；CS 八种排名指标、单侧 Top/Bottom、覆盖率及 OOS 筛选、显式 Run/批量指标和完成时点检查通过。
- 仅 TS 有效、仅 CS 有效两种 overall 研究搜索均通过；有效性最新选择、显式旧 Run、混合批量成员隔离，以及各维度指标/有效性同 Run 核对通过。
- 修正本地 SQL 后，TS-only/both 主有效维度切片与完成前/等于/后三个时点，共 5 条全部通过；这不替代仍被其他本地选样器阻断的 TS 通用分页场景。
- Backend 环境读取、日期过滤、CS summary 三方对账及公开 OpenAPI 读取通过。Schema 正常版本和字段读取通过。
- 经济结果闭环组 5 条通过；这是现有最终结果及证据的核对，不宣称重新执行了生产计算。

## 非通过结果如何归类

| 分类 | FAILED | ERROR | SKIPPED | 说明 |
| --- | ---: | ---: | ---: | --- |
| 含已确认业务 Bug | 22 | 0 | 5 | 上述 6 类，实例与 Bug 不一一对应 |
| 本地 DB 取样或基线读取阻断 | 13 | 109 | 0 | 122 实例，未进入目标业务断言 |
| 本地协议或参数判定问题 | 49 | 0 | 0 | JSON-only 拒绝解析、SSE、版本、动态游标等 |
| 生产反馈状态能力不可用 | 15 | 4 | 0 | 当前 Key 缺工具，对应表也不可用，未进入功能验收 |
| 待明确返回语义或追溯契约 | 11 | 0 | 0 | forecast/publication、unknown/null、重复 refs、环境状态及 helper 证据 |
| 批量公式详情受响应大小限制 | 2 | 0 | 0 | 大批 executable 读取受限，未证明单因子也不可读 |
| 历史公式证据记录时间契约 | 1 | 0 | 0 | 历史 completed/recorded 时间关系，不外推当前数值 |
| 技术审计字段观察 | 4 | 0 | 0 | 固定标题：计算结果审计记录缺少 request_id |
| 样本、证据或契约缺口 | 0 | 0 | 126 | 详见下文及逐节点报告 |
| 当前无状态服务不适用 | 0 | 0 | 2 | 依赖 MCP Session ID 的两个场景 |
| 合计 | 117 | 113 | 133 | 原始状态未被人工归类改写 |

本地 DB 阻断为 122 个实例，不是 122 次独立查询：summary_sample 102、slice_sample 9、related_slice_samples 6、metric_scope_snapshot 5。共享 fixture 会把一次异常传播到多个实例。原始报告保留的驱动异常为 MySQLInterfaceError，部分首次失败约 61 秒，不能仅凭耗时冒充已捕获 TimeoutError。该分组只归类具体失败节点，不把 CS 已通过的其他取样和链路一起判失败。

本地取样默认要求一个完整 scope 内 3～20 个因子，并对历史指标质量设置前置，TS seed 存在不等于符合这个小分区条件。生产已有 scope/Run 索引，未根据这些错误推断“生产缺索引”。证据：[DB 逐节点分类](../reports/factor4-production-local-db-node-classification-20260911.json)。

## 仍未完整验收的场景

1. **TS 通用搜索、排名、scope 和通用切片矩阵**：先修复上述本地大范围选样器，保留完整分区和 PIT 断言，再复跑 122 个受影响实例。已通过的 TS 精确 Run/validity/切片分支不受此结论覆盖。
2. **评分和准入边界自然样本**：覆盖率恰好 0.70、样本量 29/30/31、t-stat 1.96、OOS/sign consistency/fold/净收益边界，以及 null、回退和 clip 端点等并未全具备。不能用当前普通样本通过替代边界通过。
3. **发布结果的完整冻结身份与权重**：有路由的两个环境仍缺部分冻结权重/候选配置；metric-to-formula、raw schema、父子版本/权重关系缺强绑定。523 因子、6,276 指标的当前批次链路不能据历史未绑定公式完成验收。
4. **推荐回查与六类预测命中链路**：部分标签没有 forecast 与发布结果在同一时点相交的样本；本轮推荐回查缺少可执行的非空样本，不能证明推荐到公式/指标回查的完整闭环。六类指标都存在不等于六条使用链路都已完成。
5. **母因子聚合、主题命中和组合筛选**：缺完整 parent child_aggregate 小分区、可区分的双门槛交集或主题样本；CS 普通排名通过不替代这些分支。
6. **反馈、取消及回滚**：生产反馈两个工具及对应表不可用；取消、回滚缺真实持久化终态。本轮未写入或制造生产样本。
7. **需明确的结果契约**：无当日 forecast 时是否跨日复用、无 forecast 时是否仍返回 publication、unknown 输出允许 null 与否、重复 ref 保留位置还是去重、环境 failed 状态与增量 route 发布关系、同分次级排序规则。
8. **技术观察**：daily/batch/metric/route 缺 request_id，历史公式 evidence 的记录时间可能涉及时区或先后顺序；不列为已确认计算数值错误。

## 本轮已修正的本地问题及验证

- 发布选择及终态用例支持明确声明的增量 partial_fail，仍要求 published/active 与指针一致；终态复跑 1 PASS、2 无样本 SKIP。
- 消费链初始范围改为索引分批发现；CS 切片移除固定 50 页上限，仍验证 cursor 前进、独立 DB 集合、scope 和时间。
- Backend summary 先读取公开返回的真实 ID，再定位 DB 与 MCP；不要求因子全历史小于 20 条，也不声称覆盖全部历史排序。
- 研究目录按固定 as_of 排除之后创建的因子；unknown 节点的统计和分组差异消失，仅保留 null 返回语义待明确。
- slices_for_summary 使用派生分组解决 ONLY_FULL_GROUP_BY，生产 SQL 模式未改变。TS-only/both 及三个完成边界全部复跑通过。
- 大 JSON 保真解压、MySQL 连接压缩及必要字段投影减少传输；未删除实际断言消费的值、源码或版本证据。
- 对相关改动运行相应离线反例及定向线上验证；离线数量不计入 171/691。最终 git diff --check 通过。

固定中文问题名继续以 [Bug Registry](factor4-bug-registry.md) 为准。本轮确认标题不因多次输出或参数变化而重命名。
