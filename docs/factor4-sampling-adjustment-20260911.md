# Factor 4.0 生产只读取样调整 — 2026-09-11

## 改动与边界

针对前轮 124 个 DB 取样/基线阻断实例调整本地实现，未改业务断言、生产数据、索引、凭据或连接超时。生产会话仍强制只读。原问题不是所有 DB 查询失败：精确主键完整读取约 0.07 秒，而代表性全局 TS 取样 SELECT 超过服务端 70 秒执行上限。

- `summary_sample`：先按覆盖索引发现周期/窗口/市场范围，再轮流检查各范围的币种/窗口候选。避免大范围中的大量同类币种耗尽搜索预算。精确 scope 内先确认所有 completed 因子成员和原有指标前置，再完整读取每因子的最新结果与对应公式证据，保留同一 as-of，最新 null 不回填旧值。
- `metric_scope_snapshot`：先发现公开可筛选的 interval/universe，再读取候选完整历史。使用第 50,001 行作为超过基线预算的证据；超限就换候选，不能把截断数据交给断言。完整分区在本地按 scope/factor/run 聚合最大 period_end；不增加隐藏的 factor、window 或版本筛选。
- `slice_sample`：按主键范围分页发现紧凑 summary 身份，再按具体 Run、因子、scope、symbol 查询切片，避免全 TS 范围关联分区表。真实切片数量满足要求后，完整读取该范围所有切片。满页时从最后一个 ID 继续，不跳过同一 ID 范围余下记录。
- 无自然样本与搜索预算耗尽明确区分。只有候选空间已遍历完才能返回 `None`；未检查完时抛出 `BLOCKED_SAMPLE_DISCOVERY`，不能伪报 SKIP 或 PASS。
- DB 游标、事务和 Repository 清理保留最初查询异常，清理失败不覆盖原错误；安全异常保留数字 errno，不输出驱动敏感正文。

## 本地验证

完整离线单测：**1969 passed**。这不是线上业务通过数量。新增语义测试使用 SQLite 执行实际 SELECT，仅做占位符和 null-safe equality 的方言转换；涵盖完整历史、最新 null、完成时间边界、母子类型、profile 轮询、尾页、预算哨兵、真实切片数量、范围隔离及多层清理失败。

证据：[离线 JUnit](../reports/factor4-sampling-adjustment-unit-20260911.xml)。

## 生产取样验证

最终实现的 8 类完整取样全部成功，正式 60 秒读取时限保持不变。下列耗时是各 Repository 方法完整执行耗时，不只是候选 SELECT；不同方法可能读取不同的自然样本。

| 入口 | 完整执行耗时 | 返回基线记录 |
| --- | ---: | ---: |
| TS 币种汇总 | 8.0104 秒 | 6 个因子最新汇总、6 条公式证据 |
| TS 聚合汇总 | 11.2517 秒 | 4 个因子最新汇总、4 条公式证据 |
| CS 聚合汇总 | 8.2713 秒 | 3 个因子最新汇总、3 条公式证据 |
| TS metric scope | 6.8773 秒 | 69 条完整历史聚合记录 |
| CS metric scope | 1.9034 秒 | 74 条完整历史聚合记录 |
| TS 切片 | 3.2313 秒 | 730 条 |
| CS 切片 | 2.9109 秒 | 730 条 |
| TS 币种切片分页前置 | 2.4865 秒 | 30 条 |

证据：[最终取样验证](../reports/factor4-production-sampling-fixcheck-20260911-065110Z.json)。此前首次调整的 7/8 成功结果另存原始报告，不替换为最终数据。

## 受影响实例回归

14:52:12 启动此前 124 个阻断实例的独立复跑，覆盖 25 个测试函数、6 个模块。执行清单与上轮一致，未改用例选择和断言。pytest 完整耗时 360.29 秒（约 6 分钟），上一轮同组为 1339.36 秒（约 22 分钟）。

| 终态 | 本轮 | 上轮同组 |
| --- | ---: | ---: |
| PASSED | 96 | 0 |
| FAILED | 9 | 13 |
| ERROR | 0 | 109 |
| SKIPPED | 19 | 2 |
| 合计 | 124 | 124 |

本轮所有选中节点均有新终态。本地 DB 取样/基线读取错误及 `BLOCKED_SAMPLE_DISCOVERY` 均为 0；原 122 个 DB 阻断实例不再卡在该阶段。9 个 FAILED 已进入请求或业务断言，不能把它们自动登记成 9 个独立产品 Bug，也不能把 19 个 SKIPPED 算作通过。

失败按可见现象归为四组，待原始响应和契约核实，不据此修改正式 Bug 名称：

- 4 个：研究搜索及组合筛选返回成员的有效性状态与 DB Oracle 不一致，断言为 `research_validity:member_status`。两条 unknown 单独查询的实际成员数分别 227、860，与预期数量相同且已完整遍历；不能误报成分页数量不一致。
- 3 个：TS scope 完成时间前/等/后一微秒查询，各预期 26 个 scope、实际返回 21 个，出现 `scopes:page_cardinality / scopes:missing_identity`。当前时点同分区的 limit 1/100 查询通过，差异集中在历史 as-of 验证。
- 1 个：切片结束时间恰好等于边界的请求返回 `METRIC_SCOPE_NOT_FOUND`。
- 1 个：切片工具参数边界专项出现 `MCP text is not complete JSON`，需核对原始响应与本地解析，不能仅凭此认定计算或数据错误。

### 失败现象的后续最小复核

按用户要求整理 Bug 时，追加最小生产只读取证。原始 pytest 仍保持 96 PASS / 9 FAIL / 19 SKIP，以下裁决不改写执行终态。本次新增确认业务 Bug 为 0；历史暂缓 Bug 1 类仍复现，历史修订可见性 1 类待契约核实。

| 失败现象 | 实例 | 复核结论 |
| --- | ---: | --- |
| 有效性成员状态差异 | 4 | unknown 查询与双指标组合筛选的代表请求仅出现输出 null、本地 CASE 映射 unknown 的差异。组合筛选完整 2 成员、Run、指标一致；同 scope 统计 227 与 DB 一致。响应含 LEGACY_VALIDITY_COMPATIBILITY_FIELD，未找到要求输出非空 unknown 字符串的契约；不计业务 Bug，其他两变体未额外重跑。 |
| 历史 TS 指标范围查询的修订可见性规则不明确 | 3 | 历史 as-of 仍为 Oracle 26、API 21；当前为 31/31。缺少的 5 scope 对应 13 条 metric，均 rolling / v20260820_ts_rolling_blocks，当前行已在 8 月 31 日更新，不能证明这些当前字段在 8 月 18 日已存在。新基线与原 SQL 聚合的 69 行完全相同；取样改造未造成差异，但原历史 Oracle 的可见性前提仍需契约/不可变历史证据。 |
| Slice 结束时间边界错误 | 1 | 原固定中文标题不变，维持用户指定的 DEFERRED。本次新样本 sub_factor:410、slice 1050339383，end 恰好为 2026-09-10T00:00:00Z 返回 METRIC_SCOPE_NOT_FOUND；仅加 1 微秒即返回该切片。仍复现，不重新纳入当前新增/开放 Bug。 |
| 参数校验错误的文本响应被误判为 MCP 返回格式错误 | 1 | 原固定测试问题标题不变。合法 baseline 成功；未知参数被服务端明确以 isError=true 和纯文本拒绝，本地 JSON-only 判定器误报，不是成功响应数据损坏。 |

历史 scope 的代表复现请求（生产 MCP，先完成握手；鉴权沿用本机配置）：

```json
{"jsonrpc":"2.0","id":"scope-pit-review","method":"tools/call","params":{"name":"factor_list_metric_scopes","arguments":{"kind":"sub_factor","ic_scope":"time_series","interval":"15m","universe_key":"altcoin","as_of":"2026-08-18T15:55:22.514431+00:00","limit":100}}}
```

实际返回 21 条、truncated=false、无下一页；按现有仅依据 Run 完成时间的 Oracle 是 26 条。将 as_of 分别减/加 1 微秒同样复现，不能解释为已证明的单点等号边界 Bug。需要明确修订可见性取 created_at、completed_at、updated_at 还是独立发布时间，或提供当时已发布 scope/version 的不可变证据后再裁决。

证据：[unknown/null 最小复核](../reports/factor4-production-unknown-validity-diag-20260911.json)、[历史 scope 请求与逐行生命周期](../reports/factor4-production-scope-pit-triage-20260911-071505Z.json)、[切片边界及文本拒绝原响应](../reports/factor4-production-slice-failure-classification-20260911-071542Z.json)。初次新增诊断曾在 HTTPS 握手超时，独立重试后成功；该技术失败不混入 124 实例原始结果，不新增服务 Bug。

19 个跳过包含当前响应没有库状态投影 2 个、有效性正例不足 2 个、双指标独立反例不足 4 个、同 Run 关联切片形态不足 2 个、缺少明确 direction_sign 2 个、覆盖率数值缺失 4 个、Top/Bottom 重叠选择契约不足 3 个；无一是 DB 读取异常或搜索预算耗尽。

这次优化改变了自然样本的选择顺序。本轮通过证明所选真实样本和相应断言通过，不代表历史指定因子或未重跑的其他 Bug 已修复；也不将这 124 个结果拼成新的 691 实例全量结果。

证据：[本轮执行清单](../reports/factor4-production-blocked-rerun-20260911-145212-manifest.json)、[完整节点结果](../reports/factor4-production-blocked-rerun-20260911-145212.json)、[JUnit](../reports/factor4-production-blocked-rerun-20260911-145212.xml)、[原始执行日志](../reports/factor4-production-blocked-rerun-20260911-145212.log)、[独立逐节点复核与上轮比较](../reports/factor4-production-blocked-rerun-20260911-145212-review.json)。
