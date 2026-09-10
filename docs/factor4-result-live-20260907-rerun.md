# Factor 4.0 结果级全量复跑记录

日期：2026-09-07；本次为 Token 额度恢复后的真实测试环境复跑。
范围仅为现有 Factor 4.0 结果级用例，使用 MCP、Backend 和测试数据库只读核查。
没有修改测试断言、配置或数据库，没有触发计算、发布、反馈或组合因子流程。

## 执行结果

```bash
cd /Users/wrh/Downloads/QuestTest
/bin/bash -o pipefail -c 'python3 -u -m pytest tests/cases/factor4 -v --live --env test --tb=short --junitxml=reports/factor4-result-live-20260907T1935.xml | tee reports/factor4-result-live-20260907T1935.log'
```

- 执行开始：2026-09-07 19:36:09 +08:00；耗时 2041.311 秒，约 34 分钟；退出码 1。
- 收集 689 个参数化实例；51 个原始计算/技术专项按既有范围排除；pytest 选择 638 个。
- 结果：369 passed、6 failed、0 errors、263 skipped。
- skipped 包含 139 个历史暂缓和 124 个本轮未完成验收实例；后者混合样本、证据、规则阻塞及测试判定问题，不能统称 124 个产品 Bug 或缺失样本。
- 当前结果级非暂缓范围为 499 个实例，即 369 + 6 + 124；本轮没有新增用例扩充数量。
- 沿用测试配置中的同一 Token（脱敏标识 `naf_mcp__gO-...ivnfp0`）；没有环境变量 Token 覆盖。MCP 主机为 `test-factor-frontend.questvector.ai`。
- 额度预检成功；全程没有 `EXPORT_BUDGET_EXCEEDED`，也没有 setup error。
- 与 `factor4-result-live-20260907T1710.xml` 按相同 node 对照：原 250 个 passed 保持通过；104 个 skipped 转 passed；15 个 error 转 passed；5 个 failed 仍 failed；1 个 skipped 转 failed；263 个仍 skipped，但其中部分阻塞原因已改变。

机器报告：`reports/factor4-result-live-20260907T1935.xml`、同名 `.log`。
公式只读复核：`reports/factor4-formula-catalog-recheck-20260907T1935.jsonl`。

## 失败裁决及复现

本轮没有从这六个失败中新增确认“计算数值错误”或“接口返回值错误”的产品 Bug。
这不代表全部通过：正式结果的公式追溯缺口已经确认存在，环境快照差异仍待选择契约裁决。
固定中文标题与问题性质分开保留，不因为 pytest 显示 FAIL 就改变归属。

### 公式绑定缺证据

- 性质：正式发布结果的追溯证据缺口；不是已证实的公式数学错误。
- 86 个独立因子引用对应 98 条 active eligible route，其中 batch 6 有 86 条、batch 7 有 12 条。
- 32 个子因子在 `factor_ic_run_formula_evidence` 中没有任何记录，全部关联正式 batch 6，不能归因于 QA batch 7。
- 示例：`sub_factor:103 -> route 21022 -> batch 6`，运行公式证据数量为 0；当前可执行定义正常返回且与 DB 一致。
- 其余 54 个引用存在某次 completed evidence，但这不等于已证明与当前发布 route 的精确绑定。
- 另一个结果级 Case 检查正式批次全部 5724 条指标时发现缺少 `run_id/formula_hash/formula_version` 强链接。这是更广的绑定缺口，不能与 32 个因子相加计数。
- 本次没有发现 MCP/DB 的 `calc_logic/source_detail_id` 差异、批量漏项或重复项；29 个已批准 schema 字段检查正常。
- 影响：无法证明已发布结果使用的确切运行公式；当前定义正确不能替代历史执行证据。历史批次必须保留何种证据仍需契约确认，不凭缺证据断言数值算错。

子因子 ID：

```text
49, 62, 65, 80, 103, 106, 151, 158,
160, 163, 172, 194, 207, 212, 216, 217,
256, 339, 388, 417, 433, 439, 455, 461,
526, 527, 545, 549, 577, 583, 886, 887
```

复现：读取 active eligible route，按引用读取当前公式及数据库定义，再查询对应运行公式证据；上述 32 个引用证据计数均为 0。

```bash
python3 -m pytest tests/cases/factor4/test_formula_catalog_business.py::test_all_active_formula_projections_and_approved_inputs --live --env test -v --tb=short
```

### 环境快照缺失日期与当时可用环境记录不一致

- 性质：沿用 `CANDIDATE`；日期差异仍复现，额外环境选择条件未确认。
- batch 6 的冻结区间为 `2024-09-02` 至 `2026-09-01`，`missing_dates` 包含 `2024-09-02`。
- 该日 daily `1341` 为 `fact / CHOPPY_UP / ready / revision=1`；`available_at` 和 `created_at` 均早于冻结时点超过一天。
- batch 7 复用同一快照，两个失败记录只算一个底层差异。
- 若 missing 表示当时没有可用 ready 日历，则不应包含该日；额外筛选规则尚未明确，也没有证据证明此差异已经改变因子数值或排名。
- daily `2068` 为 not_ready，仅属于选择规则阻塞，不登记第二个 Bug。

复现：在同一只读事务读取冻结区间、as_of 和 missing_dates；查询该完整区间的日历修订并按 as_of 选择可见记录；检查 `1341` 的 ready 状态及创建时间。完整时间证据保留在 Bug Registry 同名记录。

```bash
python3 -m pytest tests/cases/factor4/test_environment_closure_business.py::test_frozen_missing_dates_match_full_range_daily_history_at_batch_as_of --live --env test -v --tb=short
```

### `new` 状态子因子目录分页验收失败

2026-09-08 更新：用户补充防导出设计，文档确认单链达到预算后终止 cursor 为预期行为；本项已判为非产品 Bug。测试判定器已修正，目录定向复验 14 passed，其中本项按受限读取验收通过（2000/3421 个因子），不宣称全库完整性通过。以下是 9 月 7 日的历史诊断事实，原报告不改写。最新规则和验证见 `docs/factor4-existing-case-corrections.md` 的目录预算补充。

- 性质：明确的游标预算终止，加上测试判定器未处理该状态；不等于 Token 日额度再次耗尽，也没有证明服务静默漏数据。
- 查询 `factor_search(kind=sub_factor, library_status=new, limit=50)`，共返回 40 页、2000 个唯一 ID。
- 第 40 页返回 `next_cursor=null`、`truncated=true`、`CATALOG_CURSOR_BUDGET_REACHED`；同次响应 catalog quota 为 `limit=1200 / used=40 / remaining=1160`。
- DB 前后稳定为 3420 个唯一 ID、4281 条分类关联行。差集 1420 是未遍历到的已有记录，不是数据库缺少的样本。无重复或额外 ID。
- 最后请求 ID：`44419847-66d4-42b3-a7d2-87c652420cc8`。
- 工具说明公开表示 bounded page 且建议不自动遍历 cursor，但未明确数值为 40 页/2000 条；全量导出是否属于此工具承诺仍需确认。
- `service/factor4_catalog_extension_service.py` 将 `truncated` 强制等同于 `bool(next_cursor)`，并把预算终止后的集合与全库集合比较，造成这次失败。应单列测试代码修正，而非直接登记目录丢失 Bug。

复现：按上述参数逐页使用返回的 cursor；最后一页检查 warning、truncated、next_cursor 和剩余额度，再与数据库去重集合比较。不要把 1420 个未访问 ID 当成静默丢失。

```bash
python3 -m pytest 'tests/cases/factor4/test_catalog_extension_business.py::test_catalog_every_kind_and_status_returns_database_members[new-sub_factor]' --live --env test -v --tb=short
```

### 成本结果证据不完整

- 性质：人工 QA batch 7 的证据缺口，不是已证实的成本计算错误。
- 仅 QA metrics `6457-6480` 缺少 `turnover_rate/net_return/sharpe` 列值及对应 payload；route 的成本证据也缺失。
- profile 为 `qa_six_labels_20260905`，规则为 `qa-six-label-v1`，配置为 `qa-fixture-v1`，代码版本为 `sql-fixture-20260905`。
- 当前判定器未按已支持评分版本区分上述人工数据；正式 batch 6 未在本次成本检查中报告差异。

复现：读取 batch 7 的 24 条 metric，比较三个列值、payload 和 route scope evidence，查看缺失字段；不能把 null 当成费用计算值。

```bash
python3 -m pytest tests/cases/factor4/test_backend_three_way_business.py::test_published_cost_fields_and_route_scope_evidence_match_final_metrics --live --env test -v --tb=short
```

### 因子定义版本证据不完整

- 性质：同一 QA batch 7 的冻结定义证据缺口，不是正式批次版本混用。
- metrics `6457-6480` 缺 `metric_identity.definition_factor_version`。
- 成员 `161368/161369` 标记 `test_data=true`；已有 `factor_version` 与 QA 冻结版本一致，缺的是独立 definition 版本字段。

复现：查询 batch 7 的冻结成员与这 24 条 metric_identity，先确认已有 factor_version 一致，再检查 definition_factor_version 是否存在。

```bash
python3 -m pytest 'tests/cases/factor4/test_lifecycle_final_state.py::test_terminal_metrics_use_frozen_factor_membership[direct-subfactors]' --live --env test -v --tb=short
```

### OOS 时间证据不完整

- 性质：同一 QA batch 7 的 OOS 证据缺口，不是已证实的未来数据泄漏。
- metrics `6457-6480` 的失败均为 `oos_folds_missing`；没有报告真实 OOS 越过 as_of、方向冻结超时或未来样本冲突。

复现：读取 batch 7 的 as_of 和这 24 条指标的 OOS payload；因为 folds 不存在，无法进一步验证逐折时间，不能改报为越界。

```bash
python3 -m pytest tests/cases/factor4/test_temporal_final_evidence.py::test_final_oos_periods_and_direction_are_not_after_batch_asof --live --env test -v --tb=short
```

## 另行确认的测试判定器问题

固定中文标题：推荐结果回查误报指标快照变化。

- 对应一个 sub_factor 推荐回查 SKIP，不在上述六个 FAIL 中。
- 发布指针前后稳定；检查 batch 7 六环境的 12 条 route 所引用 TS metric `6457-6468`，每条仅 `oos_retention` 存在比较来源差异。
- 原表列为 `Decimal("0.900000000")`，`metric_payload.oos.retention` 为空，CalculationRepository 从 JSON 投影得到 `None`；其余比较字段一致。
- `factor4_read_repository.py` 读取原列，`factor4_calculation_repository.py` 将 JSON 路径别名为同名字段，`factor4_recommendation_replay_service.py` 直接比较两种投影，误报 `recommended_factor_metric_snapshot_changed`。
- 没有观察到实际数据漂移或 float/Decimal 误差；字段与 JSON 是否必须同步仍需另按契约判断。
- 同一 Case 仍有独立公式绑定等阻塞，不能剔除该误报后把整个 Case 改算通过，也不能直接将 124 改成 123。

复核入口：`test_recommendation_replay_business.py::test_recommended_factors_replay_formula_and_metrics_in_the_same_publication[sub_factor]`；诊断只读既有 Repository，未重跑流程或修改判定器。

## 通过范围与剩余缺口

以下历史标题对应的当前结果级用例已通过，但不据此将未纳入范围的历史公式/论文专项一并关闭：

- Backend 环境日期精确过滤遗漏已有环境记录：fact、forecast 均通过。
- Backend 指标周期时间戳时区转换错误（整体偏移 8 小时）：TS、CS 均通过。
- 发布摘要路由数量与实际有效路由数量不一致：六标签摘要数量和完整发布身份均通过。
- 前轮 15 个 TS-symbol research setup error 本轮全部通过；TS/CS 任一维度有效的 research overall 分支通过。signed 模式仍有独立证据阻塞，不能说所有排名模式已通过。

当前尚未完整验收的主要原因：

| 缺口 | 本次实际表现 | 验证边界 |
| --- | --- | --- |
| 公式绑定缺证据 | 正式批次 5724 条指标缺精确 run/hash/version 链接；32 个已发布子因子完全没有运行公式证据 | 缺的是结果追溯，不要求恢复原始计算过程 |
| 冻结权重和入选契约 | 未提供可解析 TS/CS profile 权重；全部候选是否必须发布仍待规则 | 无法独立裁决合成分数及完整发布候选集合 |
| QA 版本及字段 | `qa-six-label-v1` 无对应已确认评分规则；成本、OOS 和定义字段不全 | 六标签 route 有样本，不代表六标签评分闭环都通过 |
| 自然边界和缺失值 | 8 个参数化实例共缺 87 个 `(scope, branch)` 样本分支 | 准入边界 16、null 21、Rank 回退 12、clip/惩罚 38；87 不是新增 Case 数 |
| signed 排名方向 | 6 个实例缺候选全集的显式 `direction_sign` | 不能用观察到的排名反推方向自证正确 |
| Top/Bottom 重叠 | 14 个实例缺交集排除/补位规则 | 属于契约阻塞，不直接判重复或漏选 Bug |
| 排名 coverage | 4 个实例没有 coverage 值可作门槛核验 | 属于输入证据缺口 |
| 其他终态样本 | 缺完整母子聚合、跨 available_at 修订、失败/回滚与被替代发布等现存样本 | 仍只读最终状态，不触发故障或发布来补样本 |

上述原因会在同一个 Case 内重叠，不能按此表相加得到 124。混合 Case 的部分断言已经检查通过，但整体仍因未完成分支记为 SKIP。
139 个历史暂缓和 51 个默认排除专项不重新计入本轮待补项。

例如评分 Case 实际已检查 5724 条正式指标、68370 项字段断言，未发现失败；因另 24 条 QA 指标评分版本不支持，整个实例仍记为 SKIP。自然分支中的 rounding TS/CS 两个实例已通过。

124 个实例按主要业务类别互斥归组如下；这是执行实例数，不是独立问题数：

| 主要业务类别 | 阻塞实例 |
| --- | ---: |
| 六环境准入分支、冻结权重与候选集合 | 32 |
| 评分、准入、OOS、经济结果证据及版本 | 8 |
| 公式版本与输出链路 | 15 |
| 环境指标、标签矩阵正向样本 | 11 |
| 自然边界、null、回退、clip | 8 |
| 环境时间与 PIT | 8 |
| 排名方向、Top/Bottom、coverage | 24 |
| 同分排序契约 | 2 |
| 母子因子与生命周期终态 | 8 |
| 推荐回查 | 2 |
| KB、反馈、validity、切片时间前置 | 6 |
| 合计 | 124 |

## 后续衔接

本轮已完整结束，没有仍在执行的测试任务。原始报告保持原状，不将人工裁决写回 pytest 结果。
后续优先修正目录预算终止和推荐字段投影两个测试判定问题，再处理结果级证据与规则缺口；本轮未实施这些修改。
