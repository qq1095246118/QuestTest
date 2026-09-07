# Factor 4.0 结果级验收范围

更新：2026-09-07。本次只落实用户确认的用例合并与范围分离，不扩展新的业务场景，也不执行真实环境回归。

## 验收边界

以测试环境已经持久化的计算结果为输入，核对准入、评分、排名、route 和 MCP 输出。
不独立验证公式算子、原始 IC、持仓收益或母子聚合数值，不要求原始计算实验为结果级验收提供前置。
已有公式身份、版本、表达式投影、schema 输出、最终发布指针及同批次重复读取检查仍属于结果级范围。
组合因子台不在此次改动范围内。

后续同日完成的 12 项旧 Case 误报/漏报修正及其验证见
[现有 Case 问题修正](factor4-existing-case-corrections.md)；以下范围选择和历史数量口径不变。

## 执行选择

| 范围 | 标记 | 默认行为 | 显式纳入方式 |
| --- | --- | --- | --- |
| 结果级业务与必要 MCP 冒烟 | 不带以下专项标记 | 保留执行；真实请求仍需 `--live --env test` | 原默认命令 |
| 原始计算及公式数学专项 | `factor4_internal_calculation` | Fixture 前 deselected | `--include-factor4-internal-calculation` |
| 协议、权限、审计、物理结构等技术专项 | `factor4_technical` | Fixture 前 deselected | `--include-factor4-technical` |
| 原已暂缓的异常、兼容、并发等场景 | `factor4_deferred` | 保持原来的 Fixture 前 skip | `--include-factor4-deferred` |

三个开关彼此独立。所有选中的真实测试仍通过原 live/test/主机白名单门禁；专项开关不代表启动计算或允许业务写入。
不能把 deselected/DEFERRED_SCOPE 当成缺数据或通过，也不能把参数化实例数当作独立函数数。
历史 139 个 deferred 实例不因本次整理重新归类。

## 合并入口

旧函数从正式收集入口中移除，不保留会再次执行同一断言的转发 Case。
`service/factor4_case_registry.py` 的原迁移来源继续存在，指向实际承接函数；历史 Bug 中文标题不修改。

| 旧入口 | 当前承接入口 | 保留的行为 |
| --- | --- | --- |
| `test_migrated_readonly_scripts.py::test_mcp_tool_inventory_is_complete_for_readonly_4_0_surface` | `test_protocol_read_business.py::test_protocol_tool_inventory_has_unique_names_and_object_schemas` | 完整 tools/list 分页、原两处必要工具的并集、唯一名称与 schema 校验 |
| `test_formula_route_audit_migrations.py::test_published_route_audit_and_db613_reconciliation` | `test_final_results.py` 内的身份、数值域、evidence、分区、环境矩阵、各环境摘要及排名重复读取用例 | 原 route 结果级断言；不重复运行旧 wrapper |

路由迁移来源 `db_route_audit_once.py`、`db613_targeted_closure.py` 和引用旧工具入口的历史脚本登记均保留。
重连/RPC ID 专项中的 schema 输出功能由已有 `test_schema_business.py` 的默认/指定版本、字段选择和重复读取继续覆盖。

## 混合用例拆分

- 默认 R0 使用结果级 Service 路径，保留准入、route 评分、排名及公式输出；不能先执行完整旧数学审计再忽略结果。
- DPO、固定 horizon 和公式数学语义留在原始计算专项。混合 catalog/detail/evidence 检查保留结果投影分支，数学分支需显式纳入。
- 独立两批计算比较属于原始计算专项；同一已完成批次的重复读取不是重新计算，仍保留默认。
- 原子发布契约、actor 审计和物理 DDL 属于技术专项；失败/取消/回滚的最终 active 指针检查仍保留默认。
- 反馈权限矩阵只将其他 owner 分支分到技术专项；当前 owner 的结果和合法不存在资源查询仍保留。

| 拆分前混合入口 | 默认结果部分 | 保留的原始计算专项 |
| --- | --- | --- |
| `test_factor4_r0_calculation_check` 原五个参数 | 原名保留四个结果参数，Fixture 调用 `run_result_checks` | `test_factor4_r0_internal_formula_regressions` 承接 `CALC-510-C`；原静态来源链入口仍显式可选 |
| DPO 与 fixed_horizon 家族检查 | `test_formula_family_current_and_completed_evidence_projections` 承接十个原候选的详情/证据输出 | 原 DPO/fixed_horizon 数学 Case 保留 |
| 全 active catalog 与 evidence 检查 | 原名保留，明确 `include_internal_semantics=False` | `test_formula_catalog_and_evidence_internal_semantics` |
| IV/RV 三个因子及 5921 的 detail 检查 | 原名保留，明确 `include_internal_semantics=False` | `test_formula_detail_levels_internal_semantics` 承接原四个参数的数学分支 |

拆分增加入口或参数实例不代表新增需求；对应迁移登记同时关联结果分支和专项分支。

## 未确定的契约

- 全部派生字段的依赖信息是否必须出现在输出：不把数据库有字段直接解释为接口必返。保留已有公开字段对账，不新增强制缺字段失败。
- 同分次级排序：保留按生产者已声明规则执行的 Case；未声明规则时仍按原 BLOCKED_DOC 处理，不自行添加 factor ID 等排序规则。

这些未定契约不是本次代码合并需要修改的产品规则；不擅自生成新规则或产品 Bug。
之前提到的原始计算实验缺口不再阻塞本轮结果级验收，历史计划只用于追溯。

## 验证记录

本次验证以离线单测、实际 pytest 收集、默认/专项选择矩阵和历史登记入口解析为准。
真实环境业务结果沿用各报告自身的执行时间与范围，本次整理不产生新的产品通过结论。

2026-09-07 范围拆分时的验证基线（不是后续补充后的现有总数）：

- `python3 -m pytest tests/unit -q --junitxml=reports/factor4-result-scope-unit-20260907.xml`：1250 passed；这是离线测试结果，不计作 4.0 真实环境业务通过数。
- `git diff --check`：通过。
- 历史登记保持 61 个来源，其中 56 个已断言化脚本、5 个非 Case 工具；原 107 项需求登记保留。
- 全部开关开启时收集到 169 个测试函数、669 个参数化实例；函数数与实例数不可混用。

| 收集方式 | 选中实例 | 排除实例 | 选中后仍暂缓的实例 |
| --- | ---: | ---: | ---: |
| 默认结果级 | 618 | 51 | 139 |
| 仅额外纳入原始计算专项 | 654 | 15 | 139 |
| 仅额外纳入技术专项 | 633 | 36 | 139 |
| 三个开关全部开启 | 669 | 0 | 0 |

因此默认结果级非暂缓范围为 **479 个参数化实例**；另有原始计算专项 36 个、技术专项 15 个、历史暂缓 139 个，合计 669 个。
默认收集的 618 个包括 139 个会按原规则跳过的暂缓实例，不能将 618 或 669 宣称为当前结果级可执行业务场景数。
专项拆分增加的入口只承接原有检查，不代表本次新增需求或已覆盖新的业务场景。

2026-09-07 去重场景补充后的现有数量：173 个函数、689 个参数化实例；默认收集 638 个，
其中原 139 个仍暂缓，默认结果级非暂缓 499 个。原始计算 36 个、技术专项 15 个的选择规则不变。
仅开启原始计算专项时收集 674 个；仅开启技术专项时收集 653 个。
新增内容、正式入口及尚未确证的发布入选规则见
[去重后结果级覆盖记录](factor4-deduplicated-result-coverage.md)。本轮仍无新的真实产品通过结论。
