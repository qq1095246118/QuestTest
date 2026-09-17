# Factor 4.0 全部用例最新结果汇总 — 2026-09-11

范围是上午全量执行的 **171 个测试函数 / 691 个参数化实例 / 30 个文件**，并逐 nodeid 合入后续推荐修复验收 1 项、DB 阻断补跑 124 项。125 个节点各覆盖一次，其余 566 个保留上午结果。此表是多轮执行后的最新状态汇总，**不是重新启动的一次 691 实例全量回归**。

| 状态 | 10:56–12:08 全量原始结果 | 后续验收后的最新状态 |
| --- | ---: | ---: |
| PASSED | 329 | 426 |
| FAILED | 115 | 108 |
| ERROR | 113 | 4 |
| SKIPPED | 134 | 153 |
| 合计 | 691 | 691 |

全套调度已结束，全部节点都有执行终态；153 个跳过和 4 个前置错误仍未完成目标业务断言，不能说全部验收通过。108 个失败也不是 108 个独立产品 Bug。

本次全量开启了内部计算、技术及历史暂缓专项，因此 691 不是只包含核心结果级业务场景。离线单测数量不计入这里。

## 三个来源与合并校验

- [全量原始节点](../reports/factor4-production-rerun-20260911.json)：2026-09-11 10:56–12:08，691 个唯一节点。
- [推荐修复验收](../reports/factor4-production-recommendation-fixcheck-20260911.json)：12:21，原失败节点变为 PASSED；当前为空推荐。
- [DB 阻断补跑](../reports/factor4-production-blocked-rerun-20260911-145212.json)：14:52:12–14:58:13，同一组 124 个唯一节点，96 PASSED / 9 FAILED / 19 SKIPPED / 0 ERROR。
- [逐节点最新状态](../reports/factor4-production-all-cases-latest-20260911.json)：保留每节点原全量状态、最新状态、来源、执行时间区间与裁决分类。
- 推荐节点与 124 个补跑节点没有交集，且全部属于原 691 集合。中间 12:41–13:03 的 124 项旧补跑已被下午新结果覆盖，不再次相加。

所有原始 JSON、JUnit 和日志保持原样。问题裁决不将原始 FAIL 改成 PASS。

## 未通过实例的完整归因

| 分类 | PASSED | FAILED | ERROR | SKIPPED |
| --- | ---: | ---: | ---: | ---: |
| 已通过 | 426 | 0 | 0 | 0 |
| 样本、证据或契约缺口 | 0 | 0 | 0 | 146 |
| 含已确认业务Bug | 0 | 21 | 0 | 5 |
| 待明确返回语义或追溯契约 | 0 | 15 | 0 | 0 |
| 生产反馈状态能力不可用 | 0 | 15 | 4 | 0 |
| 批量公式详情受响应大小限制 | 0 | 2 | 0 | 0 |
| 历史公式证据记录时间契约 | 0 | 1 | 0 | 0 |
| 技术审计字段观察 | 0 | 4 | 0 | 0 |
| 本地协议或参数判定问题 | 0 | 46 | 0 | 0 |
| 当前无状态服务不适用 | 0 | 0 | 0 | 2 |
| 用户指定暂缓问题 | 0 | 1 | 0 | 0 |
| 历史 TS 指标范围查询的修订可见性规则不明确 | 0 | 3 | 0 | 0 |

5 个确认未关闭 Bug 共关联 21 个 FAILED、5 个 SKIPPED；不是 26 个 Bug。
本地协议/参数判定 46 个 FAILED 包含 45 个全量原有判定问题和补跑新触达的 1 个纯文本错误解析误报。
返回语义/追溯、历史公式时间及历史 scope 修订可见性三类共 19 个 FAILED，目前不增加未约定的强制产品契约。
反馈状态能力仍有 15 FAILED + 4 ERROR；其中 4 ERROR 均在 submission_exists 前置触发 ProgrammingError，不能说整个项目数据库都不可用。

## 确认未关闭的业务 Bug

固定中文标题保持不变。下列五项最近的精确复现来自上午全量期间的独立只读诊断，下午 124 项动态样本补跑没有重新验收其原请求。

| 固定中文标题 | 最近精确复现时间（Asia/Shanghai） | 本轮事实 |
| --- | --- | --- |
| Backend TS 指标最小查询超时 | 09-11 11:06:35 开始 | 单条 time_series summary 查询 60.349 秒 ReadTimeout，未收到 HTTP 响应。 |
| TS 指标范围查询超时，使用端链路无法开始 | 09-11 11:13 | 1h/all/time_series/limit=1 返回 QUERY_TIMEOUT，reader_db 时限 20 秒；15m/altcoin 后续通过不覆盖此请求。 |
| 环境指标最小分页查询仍超过响应大小限制 | 09-11 11:13 | sub_factor:149、batch 7、WIDE_RANGE、time_series、limit=1 返回 RESPONSE_TOO_LARGE；相同因子 tags 也失败。 |
| DPO 公式错误地位移均线而非价格序列 | 09-11 11:13 | sub_factor:161106 当前 MCP 和 DB 表达式均位移均线；按现有负 DPO 定义应位移价格。 |
| IV/RV 因子定义与实际执行公式及输入字段不一致 | 09-11 11:13 | 161628/161629/161630 声明 ATM_IV - realized_vol，但执行式仅 close 滚动标准差、输入只有 close。 |

完整请求及预期/实际见 [上午全量复现步骤](factor4-production-rerun-20260911.md)。当前证据见 [Backend 诊断](../reports/factor4-production-backend-diagnostic-20260911-rerun.json)、[MCP 与精确 DB 诊断](../reports/factor4-production-mcp-diagnostic-20260911-rerun.json)。

两个公式问题确认的是当前定义与公开执行公式，不据此断言所有历史发布数值、分数或排名已经错误。

## 已修复、暂缓及待确认

- **Backend 推荐接口查询有效发布结果返回 HTTP 500**：12:21 验收通过，HTTP 200，正式 Backend/MCP/DB 对账 1 PASSED；当前 NARROW_RANGE / NO_ELIGIBLE_FACTOR / items=[]。不表示非空推荐排名已验证。
- 原 124 项本地 DB 取样/基线读取阻断已解除。这一组不再有 DB 错误或样本搜索预算耗尽，19 SKIP 仍保留其真实样本/契约缺口。
- 下午 9 FAILED 未新增确认业务 Bug：有效性 null/unknown 语义 4 项、历史 TS 修订可见性 3 项、本地文本解析误报 1 项、历史暂缓 Slice 边界 1 项。
- **Slice 结束时间边界错误**仍复现，按用户既有决定保持 DEFERRED，不计入上面 5 个当前开放 Bug。
- VWAP、孤儿、引用不存在文档等历史暂缓项，不因本次汇总重新打开。
- 153 SKIP 分为样本/证据/契约缺口 146、已确认 TS 入口 Bug 导致依赖跳过 5、无状态 MCP 不适用 2。

主要结果级缺口：四类环境无非空 eligible route 样本；非空推荐排序；冻结 TS/CS 权重、metric 到不可变 formula/schema 的强版本绑定；阈值端点与 null/fallback/clip 自然样本；母子聚合/版本证据；同分及 Top/Bottom 重叠规则。计算内部数据不足不扩大为本轮核心结果级必补项。

[补跑 9 项失败的后续裁决及 19 项样本缺口](factor4-sampling-adjustment-20260911.md)。

## 全部 30 个文件的最新状态

下表同样来自逐节点最新状态汇总，不是单次新执行统计。

| 用例文件 | 总数 | PASSED | FAILED | ERROR | SKIPPED |
| --- | ---: | ---: | ---: | ---: | ---: |
| [test_auxiliary_business.py](../tests/cases/factor4/test_auxiliary_business.py) | 27 | 19 | 0 | 0 | 8 |
| [test_backend_three_way_business.py](../tests/cases/factor4/test_backend_three_way_business.py) | 7 | 6 | 1 | 0 | 0 |
| [test_calculation_logic.py](../tests/cases/factor4/test_calculation_logic.py) | 3 | 1 | 1 | 0 | 1 |
| [test_catalog_extension_business.py](../tests/cases/factor4/test_catalog_extension_business.py) | 38 | 38 | 0 | 0 | 0 |
| [test_consumer_journey_business.py](../tests/cases/factor4/test_consumer_journey_business.py) | 6 | 2 | 0 | 0 | 4 |
| [test_cross_read_business.py](../tests/cases/factor4/test_cross_read_business.py) | 17 | 10 | 3 | 0 | 4 |
| [test_economics_closure_business.py](../tests/cases/factor4/test_economics_closure_business.py) | 5 | 5 | 0 | 0 | 0 |
| [test_environment_closure_business.py](../tests/cases/factor4/test_environment_closure_business.py) | 32 | 12 | 0 | 0 | 20 |
| [test_feedback_status_business.py](../tests/cases/factor4/test_feedback_status_business.py) | 17 | 0 | 13 | 4 | 0 |
| [test_final_results.py](../tests/cases/factor4/test_final_results.py) | 5 | 4 | 1 | 0 | 0 |
| [test_formula_catalog_business.py](../tests/cases/factor4/test_formula_catalog_business.py) | 55 | 23 | 8 | 0 | 24 |
| [test_formula_closure_business.py](../tests/cases/factor4/test_formula_closure_business.py) | 12 | 0 | 0 | 0 | 12 |
| [test_formula_route_audit_migrations.py](../tests/cases/factor4/test_formula_route_audit_migrations.py) | 4 | 1 | 3 | 0 | 0 |
| [test_lifecycle_closure_business.py](../tests/cases/factor4/test_lifecycle_closure_business.py) | 6 | 1 | 0 | 0 | 5 |
| [test_lifecycle_final_state.py](../tests/cases/factor4/test_lifecycle_final_state.py) | 16 | 7 | 5 | 0 | 4 |
| [test_migrated_readonly_scripts.py](../tests/cases/factor4/test_migrated_readonly_scripts.py) | 39 | 18 | 11 | 0 | 10 |
| [test_protocol_deferred_business.py](../tests/cases/factor4/test_protocol_deferred_business.py) | 81 | 42 | 36 | 0 | 3 |
| [test_protocol_read_business.py](../tests/cases/factor4/test_protocol_read_business.py) | 7 | 7 | 0 | 0 | 0 |
| [test_recommendation_business.py](../tests/cases/factor4/test_recommendation_business.py) | 13 | 5 | 3 | 0 | 5 |
| [test_recommendation_replay_business.py](../tests/cases/factor4/test_recommendation_replay_business.py) | 2 | 0 | 0 | 0 | 2 |
| [test_research_search_business.py](../tests/cases/factor4/test_research_search_business.py) | 58 | 44 | 5 | 0 | 9 |
| [test_route_candidate_membership.py](../tests/cases/factor4/test_route_candidate_membership.py) | 6 | 4 | 0 | 0 | 2 |
| [test_route_environment_references.py](../tests/cases/factor4/test_route_environment_references.py) | 1 | 0 | 0 | 0 | 1 |
| [test_run_selection_and_slice_scopes.py](../tests/cases/factor4/test_run_selection_and_slice_scopes.py) | 15 | 12 | 1 | 0 | 2 |
| [test_schema_business.py](../tests/cases/factor4/test_schema_business.py) | 11 | 10 | 1 | 0 | 0 |
| [test_scoring_environment_matrix.py](../tests/cases/factor4/test_scoring_environment_matrix.py) | 14 | 5 | 0 | 0 | 9 |
| [test_summary_business.py](../tests/cases/factor4/test_summary_business.py) | 112 | 88 | 3 | 0 | 21 |
| [test_temporal_final_evidence.py](../tests/cases/factor4/test_temporal_final_evidence.py) | 7 | 7 | 0 | 0 | 0 |
| [test_validity_and_slices_business.py](../tests/cases/factor4/test_validity_and_slices_business.py) | 15 | 15 | 0 | 0 | 0 |
| [test_validity_migration_business.py](../tests/cases/factor4/test_validity_migration_business.py) | 60 | 40 | 13 | 0 | 7 |
| 合计 | 691 | 426 | 108 | 4 | 153 |

