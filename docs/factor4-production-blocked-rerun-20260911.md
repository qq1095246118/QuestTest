# Factor 4.0 生产 DB 阻断实例定向复跑 — 2026-09-11

后续状态：本地取样优化后，同一 124 实例已于 14:52–14:58 再次完整执行，得到 96 passed、9 failed、19 skipped、0 errors，本地 DB 阻断为 0。最新结果见 [取样调整与回归](factor4-sampling-adjustment-20260911.md)。下文保留 12:41–13:03 原始执行与后续原因诊断。

## 范围与结果

在 Backend 推荐 HTTP 500 已单独验收修复后，按用户要求重新执行此前被 DB 取样/基线读取阻断的全部 124 个实例。清单取自上一轮逐节点分类，包含 6 个模块、25 个测试函数；排除 4 个反馈 ERROR 和已通过的推荐用例，没有改动测试代码、选择规则或断言。

本轮于 12:41:13–13:03:33（Asia/Shanghai）执行，pytest 耗时 1339.36 秒（22 分 19 秒），124 个唯一节点在清单、重新收集结果、原始 stdout 和 JUnit 中完全一致。

| 终态 | 上次这 124 个实例 | 本次 |
| --- | ---: | ---: |
| PASSED | 0 | 0 |
| FAILED | 15 | 13 |
| ERROR | 109 | 109 |
| SKIPPED | 0 | 2 |
| 总计 | 124 | 124 |

仍有 **122 个实例在 DB 取样/基线阶段失败**，形式上为 13 FAILED、109 ERROR。它们均记录 `MySQLInterfaceError`，没有进入目标业务断言。另 2 个实例恢复了 DB 读取，但因业务样本不足跳过；不能把它们算作完整通过。本次没有因这些前置结果新增产品 Bug。

| 模块 | 实例数 | 本次结果 |
| --- | ---: | --- |
| 跨读取及历史目录状态 | 2 | 2 FAILED |
| 切片工具参数前置 | 1 | 1 FAILED |
| 研究搜索与组合筛选 | 38 | 6 FAILED、30 ERROR、2 SKIPPED |
| Run 选择、切片分页与游标 | 10 | 4 FAILED、6 ERROR |
| 汇总指标、排名、scope | 69 | 69 ERROR |
| 有效性关联切片 | 4 | 4 ERROR |

## 两个变化项

以下两个节点上轮分别在 `summary_sample` 和 `research_catalog_snapshot` 读取时失败，本次均已执行到 Service 的组合筛选样本检查：

- `test_research_combined_filters_have_exact_intersection_and_monotone_relaxation[empty-cs_aggregate]`：当前没有满足 empty 场景、且能分别证明两项指标筛选作用的样本，`no empty research intersection with independent two-metric witnesses`。
- `test_research_combined_filters_have_exact_intersection_and_monotone_relaxation[relaxed-cs_aggregate]`：当前没有满足 relaxed 场景的上述独立样本，`no relaxed research intersection with independent two-metric witnesses`。

这证明两个样本的 DB 读取阶段本次已恢复，不证明组合筛选断言已完整执行，也不把自然样本不足认定为结果 Bug。

## 阻断与推荐 HTTP 500 的关系

这些用例直接通过 `DatabaseClient` 读取生产库，异常路径没有调用 Backend recommendations。推荐 HTTP 500 的修复不能代替本组的验收。本次新执行已确认其余 122 个实例仍被 DB 读取阻断；本轮未重跑其他 Backend/MCP 已登记问题，也不据此改变其状态。

原始 pytest 的异常包装会隐藏驱动异常正文及上下文，因此仅根据本轮 XML 不能把全部 `MySQLInterfaceError` 认定为某一个超时或 SQL 根因。

13:04:17 起另执行一次代表性只读 `summary_sample("ts_symbol")`，约 61.764 秒后失败，保留完整的脱敏异常链：

1. 最初 SELECT 在 `summary_sample:341 → fetch_one → cmd_query` 失败，异常为 `mysql.connector.errors.OperationalError`，原始 C 驱动异常同为 **errno=2013、sqlstate=HY000**。已用本机安装的 Connector/Python 错误码定义核对，2013 为 `CR_SERVER_LOST`，含义是查询过程中与 MySQL 服务端的连接丢失。
2. Repository 的 `finally` 尝试 `ROLLBACK` 时再次报 `OperationalError`。
3. 外层 transaction 再次回滚时抛出 `_mysql_connector.MySQLInterfaceError / 2013 / HY000`，最终由 `_snapshot` 包装为原始 pytest 看到的 RuntimeError。因此上层报告只显示最终回滚异常类型，遗漏了最初查询失败的位置。

当前配置的 DB 读取超时为 60 秒，但 **61.764 秒的耗时与 2013 错误不足以证明断开一定由客户端超时导致**。服务端主动断开、查询执行及网络路径等原因仍需进一步定位；本条结论仅直接确认这个代表样本，不将一次诊断的内部根因套用于全部 122 个实例。

诊断没有打印 SQL、参数、凭据或异常正文，不增加正式用例数量，也没有修改断言、连接配置或回滚实现。证据见 [原始驱动异常链](../reports/factor4-production-db-driver-diagnostic-20260911-050417Z.json)。

## 补充定位：普通读取正常，全局取样查询执行过慢

14:02 的只读诊断中，TS、CS 均成功通过索引找到记录，并按主键读取完整 67 字段，分别耗时 0.0726 秒、0.0741 秒。连接、表元信息及六条取样 SQL 的执行计划全部读取成功，排除“数据库整体不可连接或所有查询都失败”的解释。

六份执行计划均使用现有索引，但取样范围较宽：TS 取样查询的 summary 候选规模约 169.5 万条，CS 约 35.4 万条，均为优化器估算而非实际扫描计数。`summary_sample` 为选出一个 3–20 因子的小分区，先关联已完成 Run，再按十个 scope 字段分组、去重统计、筛选和排序，最后才 `LIMIT 1`。scope 取样另有派生结果物化及二次分组；slice 取样另有关联切片的存在性判断。这些工作发生在业务结果对账之前。

随后只对同一 TS symbol 首条原始 SELECT 执行一次受控验证：仅诊断进程将客户端读取时限设为 90 秒，并为 SELECT 加入服务端 `MAX_EXECUTION_TIME(70000)` 上限；正式配置和用例未修改。查询在 **70.4444 秒**后收到服务端 **3024/HY000** 执行超时错误；随后同一连接 `SELECT 1` 在 **0.1596 秒**内成功，事务清理也没有错误。这直接证明该取样 SQL 在本次生产环境执行中超过 70 秒，足以超出正式配置的 60 秒等待预算。

据此，当前主要阻断归因于本地自动化的全局样本发现查询在生产数据规模下执行过慢。此前 61.764 秒的 2013 断连与客户端 60 秒读取时限相符，但新诊断不追溯证明旧连接具体由哪一层关闭，也不把单条深度诊断等同于全部 122 个实例各自的根因证明。122 是共享前置影响的测试实例数，并非 122 条独立失败 SQL。此项与已修复的 Backend 推荐 HTTP 500 无调用依赖。

后续改进应优先限制候选发现阶段的查询范围，在确定公开可请求的 scope 后仍完整读取该 scope 的对账基线，并保留原有 as-of/最新结果语义；异常清理也应保留最初 SELECT 错误。本轮只诊断原因，未改变取样实现、断言、索引或生产配置。

- [精确读取与六份执行计划](../reports/factor4-production-db-query-analysis-20260911-060258Z.json)
- [单条原始 SQL 的受控执行结果](../reports/factor4-production-db-bounded-query-20260911-060844Z.json)

## 执行与证据

本次执行入口：

```bash
cd /Users/wrh/Downloads/QuestTest
python3 -u reports/factor4-production-retry-blocked.py
```

该入口从上轮分类中精确选择 124 个节点，重新收集核对后串行执行既有 pytest 用例。每次输出独立时间戳的报告，完整 pytest 参数及节点清单保存在 manifest 中。

- [执行清单及完整 argv](../reports/factor4-production-blocked-rerun-20260911-124113-manifest.json)
- [原始 JUnit](../reports/factor4-production-blocked-rerun-20260911-124113.xml)
- [完整 stdout](../reports/factor4-production-blocked-rerun-20260911-124113.log)
- [124 个节点的新结果](../reports/factor4-production-blocked-rerun-20260911-124113.json)
- [本轮逐节点独立复核](../reports/factor4-production-blocked-rerun-20260911-124113-review.json)

原有 691 实例全量报告和推荐修复报告均保持独立，不将不同轮次混算成一次新的全量结果。
