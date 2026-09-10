# Factor 4.0 目录修正与用例去重

日期：2026-09-08。只修改本地用例及其 Service，不修改服务器、凭据、数据库数据或组合因子台。
范围仍是已持久化结果到输出；没有新增公式数学、性能或权限专项。

## 两项本地修正

1. **旧目录入口没有按预算终止验收。** `Factor4ReadService` 的目录分页、查询、更新时间筛选及重复读取统一识别精确 `CATALOG_CURSOR_BUDGET_REACHED`，只在 `truncated=true` 且明确 `next_cursor=null` 时接受受限终态。停止该链，不等待、续取或重开查询绕过上限。已返回项仍核对 DB、身份、字段、筛选、重复及顺序；自然完结才对账全集。该规则不适用于环境 daily 分页。
2. **后续请求阻塞掩盖此前数据错误。** 状态/分类目录中后续分页或统计遇依赖阻塞时保留已读数据、已确认差异及独立阻塞原因。Case 先判失败，再处理跳过；只有没有已知差异才可因阻塞跳过。旧目录与重读入口同步遵守。契约错误仍失败，不转换成预算或依赖阻塞。

JUnit 明确记录终态、已读页数/条数、DB 唯一成员数、是否完成全集核对、统计状态和阻塞。
`BOUNDED_READ_ACCEPTED_NOT_FULL_EXPORT` 只表示服务允许范围内的返回数据通过，不表示全库导出通过。
`complete -> bounded`、`bounded -> complete` 均不能记为完整重读验证。

## 删除与承接

以下名称均为 `tests/cases/factor4/` 内入口。删除的是重复执行入口或参数，不删除承接断言。

| 已移除入口/参数 | 承接入口 | 净减实例 |
| --- | --- | ---: |
| `test_migrated_readonly_scripts.py::test_executable_detail_common_fields_are_consistent` | 同模块 `test_single_and_batch_factor_details_are_identical[executable]`，相同样本及完整 single/batch 比较；冗余 Service wrapper 一并删除 | 1 |
| `test_scoring_environment_matrix.py::test_six_environment_profile_has_routes_and_metrics_for_every_label` | `test_environment_closure_business.py::test_each_label_metrics_routes_and_summary_share_full_publication_identity`，全分区乘六标签，调用同一检查器 | 1 |
| `test_final_results.py::test_final_result_identity_and_admission_are_self_consistent` | 上述逐标签闭环，完整 route/batch/metric/版本/准入身份断言及非法标签检查 | 1 |
| `test_final_results.py::test_each_environment_summary_matches_final_routes` | 上述逐标签闭环，调用相同 `check_environment_summary` 并保留失败优先 | 6 |
| `test_final_results.py::test_final_result_partition_isolation` | 逐标签闭环的发布身份检查，加同模块 `test_final_result_ranking_is_partitioned_and_repeatable` 的连续排名及因子版本唯一性检查 | 1 |
| `test_calculation_logic.py::test_factor4_r0_calculation_check[CALC-506-A]` | `test_scoring_environment_matrix.py::test_six_label_profile_reconciles_ts_cs_weight_renormalization`，从默认单分区扩大到全部已发布分区 | 1 |
| `test_calculation_logic.py::test_factor4_r0_calculation_check[CALC-507-A]` | 最终排名重复读取用例，加 `test_lifecycle_closure_business.py::test_tied_routes_follow_only_the_producer_declared_ranking_contract`；不再由旧 wrapper 无条件报未定义 tie-breaker | 1 |
| `test_summary_business.py::test_factor_rank_signed_repeated_request_is_stable` | 同模块 `test_factor_rank_values_order_identity_and_latest_run` 的三个 signed 样本直接开启 `repeat=True`；raw/abs 不变 | 3 |
| `test_validity_and_slices_business.py::test_validity_matches_database_identity_status_and_scores` 的 `ts_only/cs_only/both x ts/cs` | `test_validity_migration_business.py::test_each_validity_shape_preserves_exact_ts_cs_evidence` 的 validity 分支；基础入口仍保留 `any x ts/cs` 两项 | 6 |
| **合计** | 删除 6 个冗余函数并收缩重复参数 | **21** |

R0 的默认 `run_result_checks` 同步只编排公式输出和准入两项，不会在移除参数后继续暗中执行评分/排名。
原始 `run_r0_checks` 及底层检查器保留；对应离线反例迁到仍在执行的正式入口。
107 项需求、61 个历史来源及固定中文 Bug 名称不变；56 个已断言化脚本的迁移映射仍全部解析到真实 Case。
当前 Bug 复现命令同步到承接入口，历史报告不改写。

## 审查后保留

- active publication 选择器、当前 route 不变量和 superseded 历史分开保留，避免缺历史样本掩盖当前状态的独立结果。
- 显式旧 Run validity 保留，不因组合端点用例中已有同一调用而让 metrics 阻塞 validity 的独立执行。
- Universe 独立集合与分区、rank 基线与不同模式、不同 limit 的 slice、首次可见与 revision 切换均存在独立断言或前置。
- 动态目录分区与全状态/分类矩阵分别覆盖小页、更新时间、重放及状态/分类关系，不整组删除。
- research、rank、metrics、validity 是不同端点，不以返回相似字段为由合并。
- 未发现无业务断言的占位 Case。缺样本、失败、原始计算/技术专项及历史暂缓，不作为删除理由。

## 最新数量

| 口径 | 清理前 | 清理后 |
| --- | ---: | ---: |
| 独立测试函数 | 173 | 167 |
| 全部参数化实例 | 689 | 668 |
| 默认收集实例，含暂缓 | 638 | 617 |
| 默认结果级非暂缓实例 | 499 | 478 |
| 历史暂缓实例 | 139 | 139 |
| 原始计算专项 / 技术专项 | 36 / 15 | 36 / 15 |

数量由实际 pytest 收集核对，不把离线框架单测混入业务 Case 数量，也不表示这些业务场景已全量执行或全部通过。

## 验证

```bash
python3 -m pytest tests/unit -k factor4 -q --junitxml=reports/factor4-catalog-dedup-unit-20260908.xml
python3 -m pytest tests/cases/factor4 --collect-only -q --include-factor4-internal-calculation --include-factor4-technical --include-factor4-deferred
```

- 最终离线：1415 passed，214 个其他模块单测未选中；包含迁移映射、依赖方向、选择范围、预算与失败优先反例。
- 独立复查后额外补充重读公共前缀变化、后续阻塞、双端阻塞、complete/bounded 混合终态；最终分页单测 41 passed，报告 `reports/factor4-legacy-catalog-replay-unit-20260908.xml`。
- 定向测试环境回归：2026-09-08 15:16:02 +08:00 开始，145.999 秒，31 项中 29 passed、2 failed、0 skipped、0 errors。报告 `reports/factor4-catalog-dedup-live-20260908.xml`。
- 24 项目录及详情全部通过；`new-sub_factor` 一链 40 页、2000 个唯一返回 ID，DB 为 3421，按受限范围通过，未续取或宣称完整导出。
- 承接闭环中的其他五标签通过；`WIDE_RANGE` 仍报“发布摘要路由数量与实际有效路由数量不一致”，本次为摘要 86、实际 active eligible 83。
- 最终排名用例报“有效路由排名不连续”/`RANK_NOT_CONTIGUOUS`，初次与重复读取均失败，未报告 publication 切换或重读漂移。额外只读 Repository 核对 batch 6 的 `all/default/WIDE_RANGE/2026-09-01`：83 条，rank 范围 1 至 86，缺少 74、75、84，无重复。与摘要差异是否同根因未确定，候选名称已保存，不重复增加产品根因计数。
- 本次没有重跑全部 478 个默认结果级实例，不将 31 项回归作为全量产品验收结论。
