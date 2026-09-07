# Factor 4.0 脚本迁移与执行记录

更新：2026-09-07。临时测试脚本的迁移记录继续保留；下表迁移基线为新增闭环用例之前的历史数量。当前用例库存及剩余场景边界见 [计算端闭环覆盖记录](factor4-calculation-closure-coverage.md)。迁移完成不表示服务器全部通过，也不表示原始 107 条需求中的写入、原始行情重算等全部分支已经执行。

## 迁移结果

| 项目 | 数量 | 口径 |
| --- | ---: | --- |
| 原始 Python 来源 | 61 | 删除后仍保留登记，基数不缩减 |
| ASSERTED / 已删除 | 56 / 56 | 真实业务断言、具体 pytest 函数映射、源文件已替代 |
| PARTIAL / REMAINING | 0 / 0 | 无未核销迁移来源 |
| NON_CASE_TOOL | 5 | 报告或风险采集工具，保留且不计业务用例 |
| 迁移基线测试函数 | 156 | 不含 tests/unit、登记自检及本次闭环增量 |
| 迁移基线业务实例 | 601 | 参数化实例；不是 601 个独立测试函数 |
| 基线当前范围 / 默认暂缓 | 462 / 139 | 暂缓项不默认执行 |

纠正：曾加入的 13 个本地 JSON fixture 场景只验证自造数据或离线算法，不能证明产品计算，已删除，不再计入业务覆盖。旧的 169 函数 / 614 实例 / 152 暂缓口径作废。本次以真实只读数据库断言替代，具体数量和执行结果见上述闭环记录。

`service/factor4_case_registry.py::factor4_script_migrations()` 保存全部函数映射、`source_removed` 和 `excluded_branches`。每个映射都指向真实业务函数，不调用旧脚本、不读取旧报告。107 条历史编号保留并按实际映射更新状态；编号的部分分支已实现，不代表整个原需求通过。

## 统一验证

- 历史全项目离线单元测试：964 passed，`reports/factor4-migration-unit-current.xml`；不是本次新增代码的验证结果。
- 2026-09-06 默认范围统一真实回归原始结果：291 passed、16 failed、294 skipped，601 个实例全部收集并分类，耗时 889.83 秒；报告 `reports/factor4-migration-final-live.xml`。随后显式开启 deferred 的回归同样收集了 601 个实例，长时间无进展后中止，未生成完整报告；等待的根因未确认，不能作为全量执行或通过结论。
- 上一轮 294 个 skip 分别为：139 个按范围暂缓、113 个服务端 `EXPORT_BUDGET_EXCEEDED`、42 个数据或契约前置不足。这不是 294 个未实现用例，也不是全部业务已验收。
- 原始 16 个 fail 中包含 2 个代理/SSL 连接失败，不能归为功能 Bug；人工六环境 fixture 缺成本、OOS 和版本证据的失败也不证明真实计算错误。
- 统一回归后修正切片 limit Case 的重复参数构造（两次取当前时间导致 end_time 晚于 as_of，且遗漏精确 Run/symbol），复用正式 Service 的完整分页与 DB 断言；两项定向重跑均 passed，报告 `reports/factor4-slice-limit-migration-final-live.xml`。未覆盖或篡改原始全量报告。
- 统一回归后修正公式审计的执行/normalized 元数据来源混用，以及声明/required/resolved 字段跨层合并问题。旧 DPO 元数据不再算成执行公式失败；真实同层冲突仍失败，无法证明的单位归一化明确阻断。新增 11 个离线反例全部通过，不能据此宣布产品公式已经通过。
- 修正后的最小公式族复核：2 skipped，仍缺可用 MCP 详情投影和强版本绑定，报告 `reports/factor4-formula-layer-final-live.xml`。未再次执行耗费全量额度的 477 因子遍历，原始静态审计与 DPO 失败不能直接作为最终产品缺陷数量。
- 原始聚合 Case 曾在首项阻断时跳过另一项已发现的静态失败，现已改为先保留全部失败再处理阻断，并补反例。回归后这些修改均通过最终单元校验；最终代码没有重新获得一份无配额阻断的全量 live 报告。
- MCP、Backend 和数据库均经过 test/live 与主机白名单校验；没有启动计算、发布或修改业务数据。
- 未开启 `--include-factor4-deferred`；暂缓用例在 Fixture 前跳过，不会因运行整个目录而偷偷执行负向、并发请求。
- 失败实例不是去重 Bug 数。固定中文标题保留在 `docs/factor4-bug-registry.md`；其中旧状态与旧数量不作为本轮结论。

```bash
python3 -m pytest tests/cases/factor4 --live --env test -q --tb=short \
  --junitxml=reports/factor4-migration-final-live.xml
python3 -m pytest tests/unit -q
python3 -m pytest tests/cases/factor4 --collect-only -qq
```

某个旧来源的复现入口从登记表取 `case_module` 与 `test_nodes`。例如：

```bash
python3 -m pytest tests/cases/factor4/test_summary_business.py \
  -k factor_rank_values_order_identity --live --env test -v
python3 -m pytest tests/cases/factor4/test_final_results.py \
  -k each_environment_summary --live --env test -v
```

## 覆盖边界

业务包括目录筛选与统计、母子详情、研究搜索、TS/CS 排名、Run 选择、有效性、完整切片分页、批量隔离、环境修订/PIT、六环境最终矩阵、分数/排名/route 对账、推荐三方、公式来源链、KB 映射与任务、Universe、Schema、反馈主体隔离、冻结成员/历史发布/审计/终态不变量。

- 只验证已持久化最终结果及明确公开字段，不宣称重放缺失的原始 bar、收益、持仓、训练过程或服务器二次计算。
- 缺多 revision、历史 superseded route、终态 parent、owned feedback、特定 KB 任务等样本时，实际发现后记录前置，不写死历史 BLOCKED、不伪造样本。
- 未明确的 feature horizon、同分 tie-breaker、发布/回滚模式、Top-up 语义，不擅自决定契约。
- 旧脚本中只有固定阻断的内部 HMAC、普通权限 PAT、Scheduler、故障注入、事务清理分支，不复制为占位 Case，也未计作运行时覆盖。
- 正常业务可使用完整 structuredContent；text JSON 完整性和双表示严格一致由独立 deferred Case 验证。
- 人工六环境最终结果 fixture 缺少成本、OOS 或版本证据时保留失败；只能证明 fixture 证据不完整，不能单凭它断言真实计算公式错误。

## 纠正旧判断

- 同一环境的不同因子 route 不是重复；唯一性包含完整因子、版本、profile 和发布身份。
- 全库水位变化不能证明一次读取修改了数据；password/authorization 字样不等于明文凭据。
- factor_rank 不能替代研究 factor_search；阈值只发送给声明支持的端点。
- 同 Run 多周期未定义唯一选择规则时，核对精确 Run/scope 的全部真实返回成员与字段，不擅自选最大 updated_at。
- 请求 limit 是上限，不保证每页恰好返回该数量；分页按真实 cursor 走完并检查重复、循环、丢页。
- 只核验实际公开字段；当前详情、不可变公式、Backend、MCP 的不同投影不能要求不存在的字段。
- 已确认不一致优先于其他样本的前置不足，不能用 skip 隐藏失败。

## 来源映射

下表模块均位于 `tests/cases/factor4/`。完整函数名、迁移说明与逐来源排除项见登记表；跨来源复用入口，不能逐行相加计算业务数量。

| 已删除来源 | 涉及的正式模块 |
| --- | --- |
| `active_formula_semantic_recheck.py` | `test_formula_catalog_business.py` |
| `auto_run_selection_recheck.py` | `test_run_selection_and_slice_scopes.py` |
| `batch_cursor_boundary_probe.py` | `test_protocol_deferred_business.py`<br>`test_catalog_extension_business.py`<br>`test_validity_migration_business.py` |
| `calc508_env_met_reconcile.py` | `test_backend_three_way_business.py` |
| `catalog_boundaries_resume_probe.py` | `test_migrated_readonly_scripts.py`<br>`test_research_search_business.py`<br>`test_protocol_deferred_business.py` |
| `catalog_deep_expansion.py` | `test_catalog_extension_business.py`<br>`test_migrated_readonly_scripts.py`<br>`test_summary_business.py`<br>`test_research_search_business.py`<br>`test_auxiliary_business.py`<br>`test_protocol_deferred_business.py` |
| `catalog_deep_readonly.py` | `test_catalog_extension_business.py`<br>`test_migrated_readonly_scripts.py`<br>`test_summary_business.py`<br>`test_research_search_business.py`<br>`test_auxiliary_business.py`<br>`test_protocol_deferred_business.py` |
| `catalog_deep_recheck.py` | `test_catalog_extension_business.py`<br>`test_migrated_readonly_scripts.py`<br>`test_summary_business.py`<br>`test_research_search_business.py`<br>`test_auxiliary_business.py`<br>`test_protocol_deferred_business.py` |
| `catalog_kb_remaining_probe.py` | `test_auxiliary_business.py`<br>`test_catalog_extension_business.py`<br>`test_summary_business.py`<br>`test_research_search_business.py` |
| `catalog_search_functional_deep.py` | `test_catalog_extension_business.py`<br>`test_migrated_readonly_scripts.py`<br>`test_summary_business.py`<br>`test_research_search_business.py`<br>`test_auxiliary_business.py`<br>`test_protocol_deferred_business.py` |
| `critical_readonly_gap_probe.py` | `test_protocol_read_business.py`<br>`test_migrated_readonly_scripts.py`<br>`test_protocol_deferred_business.py`<br>`test_recommendation_business.py`<br>`test_final_results.py`<br>`test_cross_read_business.py` |
| `cross_invariants_readonly.py` | `test_schema_business.py`<br>`test_protocol_read_business.py`<br>`test_migrated_readonly_scripts.py`<br>`test_protocol_deferred_business.py`<br>`test_recommendation_business.py`<br>`test_final_results.py`<br>`test_cross_read_business.py`<br>`test_auxiliary_business.py`<br>`test_feedback_status_business.py`<br>`test_formula_catalog_business.py` |
| `cross_scope_identity_probe.py` | `test_summary_business.py`<br>`test_migrated_readonly_scripts.py`<br>`test_run_selection_and_slice_scopes.py`<br>`test_protocol_deferred_business.py` |
| `cross_scope_metrics_probe.py` | `test_summary_business.py`<br>`test_research_search_business.py`<br>`test_validity_migration_business.py`<br>`test_run_selection_and_slice_scopes.py` |
| `current_endpoint_deep_regression.py` | `test_schema_business.py`<br>`test_protocol_read_business.py`<br>`test_migrated_readonly_scripts.py`<br>`test_protocol_deferred_business.py`<br>`test_catalog_extension_business.py`<br>`test_summary_business.py`<br>`test_research_search_business.py`<br>`test_run_selection_and_slice_scopes.py`<br>`test_validity_migration_business.py`<br>`test_formula_catalog_business.py`<br>`test_cross_read_business.py`<br>`test_recommendation_business.py`<br>`test_final_results.py`<br>`test_auxiliary_business.py`<br>`test_feedback_status_business.py` |
| `current_endpoint_functional_probe.py` | `test_schema_business.py`<br>`test_protocol_read_business.py`<br>`test_migrated_readonly_scripts.py`<br>`test_protocol_deferred_business.py`<br>`test_catalog_extension_business.py`<br>`test_summary_business.py`<br>`test_research_search_business.py`<br>`test_run_selection_and_slice_scopes.py`<br>`test_validity_migration_business.py`<br>`test_formula_catalog_business.py`<br>`test_cross_read_business.py`<br>`test_recommendation_business.py`<br>`test_final_results.py`<br>`test_auxiliary_business.py`<br>`test_feedback_status_business.py` |
| `daily_reconcile_readonly.py` | `test_migrated_readonly_scripts.py` |
| `daily_revision_precondition_probe.py` | `test_migrated_readonly_scripts.py` |
| `db_route_audit_once.py` | `test_formula_route_audit_migrations.py` |
| `db613_targeted_closure.py` | `test_formula_route_audit_migrations.py` |
| `direct_functional_deep.py` | `test_schema_business.py`<br>`test_protocol_read_business.py`<br>`test_migrated_readonly_scripts.py`<br>`test_protocol_deferred_business.py`<br>`test_catalog_extension_business.py`<br>`test_summary_business.py`<br>`test_research_search_business.py`<br>`test_run_selection_and_slice_scopes.py`<br>`test_validity_migration_business.py`<br>`test_formula_catalog_business.py`<br>`test_cross_read_business.py`<br>`test_recommendation_business.py`<br>`test_final_results.py`<br>`test_auxiliary_business.py`<br>`test_feedback_status_business.py` |
| `dpo_formula_recheck.py` | `test_formula_catalog_business.py`<br>`test_formula_route_audit_migrations.py` |
| `feedback_status_input_matrix.py` | `test_feedback_status_business.py` |
| `filter_error_kb_deep.py` | `test_catalog_extension_business.py`<br>`test_migrated_readonly_scripts.py`<br>`test_summary_business.py`<br>`test_research_search_business.py`<br>`test_auxiliary_business.py`<br>`test_protocol_deferred_business.py` |
| `fixed_horizon_adjudication.py` | `test_formula_catalog_business.py` |
| `fixed_horizon_family_audit.py` | `test_formula_catalog_business.py` |
| `fixed_horizon_formula_recheck.py` | `test_formula_catalog_business.py`<br>`test_formula_route_audit_migrations.py` |
| `formula_integrity_audit.py` | `test_formula_route_audit_migrations.py` |
| `functional_extra_r0.py` | `test_schema_business.py`<br>`test_protocol_read_business.py`<br>`test_migrated_readonly_scripts.py`<br>`test_protocol_deferred_business.py`<br>`test_catalog_extension_business.py`<br>`test_summary_business.py`<br>`test_research_search_business.py`<br>`test_run_selection_and_slice_scopes.py`<br>`test_validity_migration_business.py`<br>`test_formula_catalog_business.py`<br>`test_cross_read_business.py`<br>`test_recommendation_business.py`<br>`test_final_results.py`<br>`test_auxiliary_business.py`<br>`test_feedback_status_business.py` |
| `iv_rv_definition_recheck.py` | `test_formula_catalog_business.py`<br>`test_formula_route_audit_migrations.py` |
| `kb_topup_route_audit.py` | `test_auxiliary_business.py`<br>`test_final_results.py`<br>`test_migrated_readonly_scripts.py` |
| `lifecycle_readonly_resume.py` | `test_lifecycle_final_state.py` |
| `metric_scope_visibility_probe.py` | `test_summary_business.py` |
| `metrics_deep_minimal.py` | `test_summary_business.py`<br>`test_validity_migration_business.py`<br>`test_run_selection_and_slice_scopes.py` |
| `protocol_boundary_probe.py` | `test_protocol_read_business.py`<br>`test_protocol_deferred_business.py`<br>`test_feedback_status_business.py` |
| `protocol_gap_closure.py` | `test_protocol_read_business.py`<br>`test_protocol_deferred_business.py`<br>`test_feedback_status_business.py` |
| `rank_functional_expansion.py` | `test_summary_business.py`<br>`test_validity_migration_business.py` |
| `rank_targeted_current.py` | `test_summary_business.py` |
| `rank_targeted_recheck.py` | `test_summary_business.py` |
| `ranking_parent_snapshot_closure.py` | `test_lifecycle_final_state.py`<br>`test_final_results.py` |
| `readonly_invariant_probe.py` | `test_lifecycle_final_state.py`<br>`test_protocol_read_business.py`<br>`test_protocol_deferred_business.py`<br>`test_migrated_readonly_scripts.py`<br>`test_backend_three_way_business.py`<br>`test_final_results.py` |
| `recheck_aggregate_window_candidates.py` | `test_formula_catalog_business.py` |
| `recommendation_pit_recheck.py` | `test_recommendation_business.py` |
| `resume_rank_adjudication.py` | `test_summary_business.py` |
| `resume_slice_scopes_probe.py` | `test_run_selection_and_slice_scopes.py` |
| `route_integrity_closure.py` | `test_migrated_readonly_scripts.py`<br>`test_lifecycle_final_state.py`<br>`test_final_results.py`<br>`test_temporal_final_evidence.py`<br>`test_route_environment_references.py`<br>`test_backend_three_way_business.py` |
| `semantic_formula_scan.py` | `test_formula_catalog_business.py` |
| `slice_reconcile_probe.py` | `test_run_selection_and_slice_scopes.py` |
| `status_permission_closure.py` | `test_feedback_status_business.py`<br>`test_backend_three_way_business.py`<br>`test_protocol_deferred_business.py` |
| `targeted_5921_recheck.py` | `test_formula_catalog_business.py` |
| `targeted_formula_metadata_recheck.py` | `test_formula_route_audit_migrations.py` |
| `temporal_oracle_closure.py` | `test_temporal_final_evidence.py`<br>`test_lifecycle_final_state.py`<br>`test_migrated_readonly_scripts.py` |
| `tool_matrix_pit_probe.py` | `test_schema_business.py`<br>`test_protocol_read_business.py`<br>`test_migrated_readonly_scripts.py`<br>`test_protocol_deferred_business.py`<br>`test_catalog_extension_business.py`<br>`test_summary_business.py`<br>`test_research_search_business.py`<br>`test_run_selection_and_slice_scopes.py`<br>`test_validity_migration_business.py`<br>`test_formula_catalog_business.py`<br>`test_cross_read_business.py`<br>`test_recommendation_business.py`<br>`test_final_results.py`<br>`test_auxiliary_business.py`<br>`test_feedback_status_business.py` |
| `truerange_active_recheck.py` | `test_formula_route_audit_migrations.py` |
| `validity_boundary_deep.py` | `test_validity_migration_business.py` |
| `validity_visibility_recheck.py` | `test_validity_migration_business.py` |

## 保留工具

- `filter_error_kb_correct.py`：仅加载固定历史报告并修正 verdict/已排除兼容项，无实时接口/DB 测试入口；保留报告修订工具，不计业务 Case
- `field_runtime_mcp.py`：仅读取历史 unresolved 名单，采集当前 batch detail/raw schema 并输出字段风险证据，无可判定业务预期；保留诊断工具，schema/详情读取一致性已由正式 Case 覆盖
- `field_runtime_db.py`：仅基于历史名单统计 runtime 产出，固定 static_contract_risk_only 和 confirmed_failure_count=0；不能把已有非空历史指标当当前字段正确性断言，保留诊断工具
- `final_coverage_merge.py`：历史报告合并/恢复工具；不是业务 Case，保留以便读取旧证据
- `recover_readonly_invariant_report.py`：历史报告合并/恢复工具；不是业务 Case，保留以便读取旧证据

这些工具不计入迁移基线的 601 个业务实例，也不因保留而视为迁移未完成。

## 删除与恢复

共删除 56 个已替代的测试脚本。未删除测试配置、凭据、历史报告、PDF 资产或无关用户修改。

本次完整备份包含后来删除的 47 个脚本和 5 个保留工具：

`reports/factor4-script-migration-backup-current/tmp-before-next-migrations.tar.gz`

SHA-256：`606be0874189d3520eb802b6807ce95793dcb3a8ab48b4cec2a3b8f8758fcc20`

此前 4 个已删除脚本的备份：

`reports/factor4-script-migration-backup.Mvgg8q/original-scripts.tar.gz`

SHA-256：`81ef4fca79240695eae3f138b86f9d8c0c4d92e186e8a5592abcf7f303f45ac2`

另 5 个此前已删除来源可由 Git 恢复：`truerange_active_recheck.py`、`targeted_formula_metadata_recheck.py`、`formula_integrity_audit.py`、`db_route_audit_once.py`、`db613_targeted_closure.py`。

从项目根目录恢复备份时不覆盖已有文件；恢复后需同步迁移登记：

```bash
tar -xzvkf reports/factor4-script-migration-backup-current/tmp-before-next-migrations.tar.gz
```

备份和自动报告位于被 Git 忽略的 reports 中，不会随普通源码提交自动保存。
