# Factor 4.0 计算端剩余场景覆盖记录

更新：2026-09-07。业务断言读取测试环境已持久化结果和环境历史，不启动计算、发布、失败注入或业务数据准备。后续能力核查另执行了 MCP 握手和 tools/list、Backend OpenAPI GET 及只读数据库元数据查询，没有调用业务写入口。

范围调整：用户随后将当前验收边界限定为“计算结果到输出结果”。下文统计与 live 结果保留为调整前历史记录；原始计算实验不再作为本轮待完成项。当前合并入口、专项开关和默认执行范围以 [结果级验收范围](factor4-result-acceptance-scope.md) 为准。本次范围整理没有重跑真实环境。

## 统计口径

| 项目 | 函数 | 参数化实例 |
| --- | ---: | ---: |
| 原迁移基线 | 156 | 601 |
| 累计真实闭环增量 | 11 | 55 |
| 范围调整前 Factor 4.0 正式用例 | 167 | 656 |

调整前的 656 个实例中，517 个属于当时正常范围，139 个异常/兼容性等实例默认暂缓。这里不包含 `tests/unit`，也不是当前已经通过的数量。数量由 pytest 实际收集核对，不按需求编号、脚本来源或断言条数累加。

之前的 `test_calculation_deferred_business.py` 已删除：其中 13 个场景依赖自造 JSON、非空检查或离线 oracle，不能作为真实业务覆盖。它们未被迁移到另一处继续凑数量。

## 新增入口

以下模块均位于 `tests/cases/factor4/`，依赖 `service/` 的业务检查及受 test/live/数据库主机白名单约束的真实 DB fixture。

| 模块 | 函数 / 实例 | 实际断言 |
| --- | ---: | --- |
| `test_environment_closure_business.py` | 4 / 32 | 冻结日历 1；全区间历史独立核查 missing_dates 1；六环境指标/摘要/route 身份 6；六环境各自 TS/CS 四种有效性组合及权重 24 |
| `test_formula_closure_business.py` | 1 / 12 | 六环境不可变公式绑定 6；六环境指定原始 schema 的依赖闭环 6 |
| `test_economics_closure_business.py` | 2 / 5 | 从最终指标独立重算 E/P/OOS 评分 3；TS/CS fold 配置、时长、方向 2 |
| `test_lifecycle_closure_business.py` | 4 / 6 | 两次独立计算结果；母子冻结关系；三种失败终态指针；有明确契约的同分排序 |

一个参数化实例会检查所有读取到的发布分区。任一分区已确认失败会先报 FAIL，不能被其他分区缺证据的 skip 遮蔽。某个分区没有该分支样本时，完整场景不能计为通过，即使另一个分区已经完成部分核对。

## 原 11 个方向逐项对照

| 原方向 | 本次自动化入口 | 可以证明的范围 | 仍不能证明的部分 |
| --- | --- | --- | --- |
| 六环境互斥、完整及 daily/metric/route 标签一致 | 环境模块 `test_frozen_environment_calendar_is_exclusive_complete_and_point_in_time`；`test_frozen_missing_dates_match_full_range_daily_history_at_batch_as_of`；`test_each_label_metrics_routes_and_summary_share_full_publication_identity` | 冻结成员日期、daily ID、label、revision、schema、available_at；全区间独立读取所有历史修订，重建 as_of 可见日期并对账 missing_dates/遗漏成员；route 引用同环境 metric | 未从行情重新分类，该算法属于上游服务；混合未知时区和未明确的非 ready 修订选择规则不能猜测；历史分环境指标不等于发布当天单一标签 |
| 六环境逐项核对 route_count、metric 数量、batch、版本和 as_of | 环境模块逐 label 身份用例 | 同发布身份的 active eligible route 实数与摘要对账；存在的每 label 指标计数字段与明细对账；关联指标和版本一致 | 不存在的摘要字段不能凭空补零；不证明上游实际执行了每一种市场形态的原始算法 |
| TS 或 CS 任一维有效；两维都无效排除 | 环境模块 `test_each_label_ts_cs_admission_branch_and_renormalized_final_route_weights` | 六 label 各自 both/ts_only/cs_only/neither；实际准入证据、有效维度、metric 外键、无效维度分数清除、冻结权重归一、置信度及 route 分数 | 自然数据没有某一分支时不算通过；没有明确冻结权重不能猜默认值；任一维有效也不保证通过最低 route 分数等发布门槛 |
| rolling/diff/pct_change/rank/VWAP 窗口及 warm-up | 本轮没有新增伪业务入口；保留 `tests/unit/test_factor4_pending_oracles.py` 等离线自测 | 只能验证测试 oracle 自身的样例算法；VWAP 默认完整窗口，明确指定 min_periods 时才允许部分窗口 | 没有同一公式版本的服务端逐点输出及独立输入序列，不能判断服务端算子正确。离线通过不计产品覆盖 |
| OOS fold、缺失/零持仓 bar、换手、成本及 Sharpe | 经济模块 `test_persisted_oos_fold_counts_durations_and_directions_match_frozen_batch`；`test_final_economic_components_recompute_without_unrelated_ic_inputs` | 已落库 fold 的数量、连续时段、冻结天数和方向化 IC/Rank IC；从最终 Sharpe/net_return/drawdown/turnover/retention 重算已公开评分分量 | 不重算原始 Sharpe、换手、费用或净收益；不能验证全 bar 网格和零暴露保留；OOS 聚合 retention 缺权重/选择契约时不自行定义 |
| 修改未来数据不影响固定 as_of 历史结果 | 本轮不新增占位用例；已有 `test_temporal_final_evidence.py` 保留时间边界检查 | 只读证据可核对 fold/方向时间不晚于 as_of、修订可见性 | 没有修改未来数据并重新执行两次计算，因此未覆盖扰动实验，不能用前后重复读取或同一 hash 自比替代 |
| 母子缺失、方向、权重和关系版本不串批次 | 生命周期模块 `test_parent_metrics_reconcile_frozen_child_membership_and_relation_versions` | metric evidence 明确包含 children/relation 时，与同批次冻结成员、版本、权重、方向逐项对账 | 当前未明确输出的关系证据阻断；不把子因子 IC 求均值当作母因子算法，不独立重算母因子逐点聚合 |
| 相同冻结输入两次计算一致 | 生命周期模块 `test_distinct_successful_batches_with_same_frozen_inputs_produce_equal_results` | 只比较真实不同成功 batch/run；冻结输入/config/code/as_of 一致，且每个 metric 的实际 artifact_manifest_hash 非空一致后，再比公式身份、投影指标和 route 分数/排名/成员 | 没有符合条件的两批计算不能通过；不同实际输入不算“同输入结果不一致”；没有启动新计算；仅校验已投影字段，不声称任意 payload 字节或执行 ID 必须相同 |
| 同分排序稳定性 | 生命周期模块 `test_tied_routes_follow_only_the_producer_declared_ranking_contract` | 有真实同分组及生产者冻结的完整 tie_breaker 时，按声明字段、方向逐项核对 | 无契约是 BLOCKED_DOC；不擅自使用 factor_id/ref 升序，不把 Python 稳定排序视为业务契约 |
| 发布失败、取消、回滚的原子一致性 | 生命周期模块 `test_unsuccessful_publication_terminal_states_do_not_own_active_pointers` | 三种真实终态的最终 active/publish 状态、分区唯一性、route/batch 发布身份；没有对应终态样本明确阻断 | 只读最终结果不能观察事务中间态、故障注入或回滚事件顺序，因此未完成原子性验证 |
| formula/schema/metric evidence/route 版本闭环 | 公式模块 `test_published_routes_bind_exact_formula_and_schema` | route 全身份及 metric 引用；明确绑定的不可变 formula/run/hash/version；精确 raw schema 版本；依赖图缺字段、重复、循环及错误版本检测 | 缺强绑定时不能使用当前最新公式；缺 raw schema 版本不能拿环境 schema 代替；无法证明“修复后已重新计算”直到新 run 的整条绑定证据存在 |

## 首次闭环执行记录

首次闭环结果保留在 `reports/factor4-closure-final-live-20260907.xml`；这是当时新增 54 个实例的小批验证，不含随后补充的全区间历史用例。`reports/factor4-migration-final-live.xml` 保持为迁移阶段历史结果，不覆盖、不合并成当前通过数量。

| 模块 | 通过 | 失败 | 证据/样本/契约不足 |
| --- | ---: | ---: | ---: |
| 环境闭环 | 5 | 1 | 25 |
| 公式闭环 | 0 | 0 | 12 |
| 经济/OOS 闭环 | 0 | 0 | 5 |
| 生命周期闭环 | 1 | 0 | 5 |
| 合计 | 6 | 1 | 47 |

最终真实运行耗时 16.49 秒，无基础设施 ERROR。全项目离线单元测试 1173 passed，报告为 `reports/factor4-closure-final-unit-20260907.xml`；离线反例测试不计作产品通过。

唯一失败继续使用固定中文标题 **发布摘要路由数量与实际有效路由数量不一致**：`WIDE_RANGE` 摘要 `route_count=0`，相同发布身份的实际 active eligible route 为 86。其余五种 label 的指标/route/摘要身份用例通过。生命周期通过的是取消终态的最终指针检查，不是事务原子性通过。

47 个阻断包括：日历证据中 naive/aware 时间的时区契约未明确；部分环境没有 TS-only/CS-only 等真实组合；部分批次缺准入证据、冻结权重或采用未明确规则的评分版本；metric 缺不可变公式及原始 schema 绑定；部分最终结果缺 E/P/OOS 分量或 fold；缺同输入独立重算、母子关系证据、失败/回滚终态和明确同分规则。多个分区会同时贡献不同原因，不能把原因数量直接相加当用例数。

同名 Bug 的本次复现入口：

```bash
python3 -m pytest \
  'tests/cases/factor4/test_environment_closure_business.py::test_each_label_metrics_routes_and_summary_share_full_publication_identity[WIDE_RANGE]' \
  --live --env test -v --tb=short
```

```bash
env AUTOMATION_DB_CONNECT_TIMEOUT_SECONDS=10 \
  AUTOMATION_DB_READ_TIMEOUT_SECONDS=20 \
  AUTOMATION_DB_WRITE_TIMEOUT_SECONDS=20 \
  python3 -m pytest \
  tests/cases/factor4/test_environment_closure_business.py \
  tests/cases/factor4/test_formula_closure_business.py \
  tests/cases/factor4/test_economics_closure_business.py \
  tests/cases/factor4/test_lifecycle_closure_business.py \
  --live --env test -v --tb=short \
  --junitxml=reports/factor4-closure-final-live-20260907.xml

python3 -m pytest tests/unit -q \
  --junitxml=reports/factor4-closure-final-unit-20260907.xml
python3 -m pytest tests/cases/factor4 --collect-only -qq
```

首次增量运行的 3 个终态失败是测试字段映射错误：route 物理表没有独立 route_profile_key，须从所属 batch 派生；已修正并加反例，不作为产品 Bug。最终重跑前的 4 failed / 5 passed / 45 skipped 仅用于保留修正过程。

缺失证据不自动等于产品缺陷，也不等于已验证通过。要达到原 11 个方向的完整计算验收，还需要生产者输出对应版本的逐点结果/输入证据、完整关系与公式绑定、冻结排序契约，以及可观察的重算与故障事件；在当前仅最终结果的范围内，不伪造这些输入。

## 继续补齐的范围与实际前置

本次新增 `test_frozen_missing_dates_match_full_range_daily_history_at_batch_as_of`。共享 fixture 显式开启 `include_full_environment_history=True`；Repository 在同一只读事务内查询完整 `start_date/end_date/label_kind`，不按 members、is_current、available_at 或 LIMIT 预裁剪。范围标记能区分完整查询与旧的成员日期查询，空成员不能导致跳过历史查询。

最终重跑四个闭环模块共 55 个实例：**6 passed / 2 failed / 47 skipped**，耗时 15.13 秒，无 ERROR。报告 `reports/factor4-closure-range-history-final-live-20260907.xml`。全项目离线单元测试 **1213 passed**，报告 `reports/factor4-range-history-final-unit-20260907.xml`。本次未重跑全部 656 个业务实例。

两项失败分别为已确认的“发布摘要路由数量与实际有效路由数量不一致”，以及新候选“环境快照缺失日期与当时可用环境记录不一致”。新候选对应 batch 6/7 的 `2024-09-02`，已有 ready 日历 1341 且其创建/可用时间早于批次冻结，却列入 missing_dates；额外筛选契约尚未明确，不提前宣称根因或计为第二个确认 Bug。固定中文标题、事实和复现入口已写入 Bug Registry。

```bash
env AUTOMATION_DB_CONNECT_TIMEOUT_SECONDS=10 \
  AUTOMATION_DB_READ_TIMEOUT_SECONDS=20 \
  AUTOMATION_DB_WRITE_TIMEOUT_SECONDS=20 \
  python3 -m pytest \
  tests/cases/factor4/test_environment_closure_business.py \
  tests/cases/factor4/test_formula_closure_business.py \
  tests/cases/factor4/test_economics_closure_business.py \
  tests/cases/factor4/test_lifecycle_closure_business.py \
  --live --env test -v --tb=short \
  --junitxml=reports/factor4-closure-range-history-final-live-20260907.xml
```

这关闭了“没有独立核实 missing_dates”的实现缺口，但不代表原 11 个方向全部覆盖。剩余五类完整业务实验为：算子逐点重算、逐 bar 经济收益重算、母子逐点聚合、未来数据扰动、发布事务原子性。

2026-09-07 已完成真实能力调查，结论不是泛指“数据库没有数据”：

| 已核对的来源 | 已确认事实 | 不能据此完成什么 |
| --- | --- | --- |
| MCP tools/list | 22 个工具，无后续页；有指标、公式、切片和 schema 读取，没有计算、输入注入或故障实验工具 | 无法运行自定义算子样本、未来扰动或故障实验 |
| 测试 Backend OpenAPI | 有建批、查询、发布、回滚、current publication；建批配置为自由 JSON | 自由 JSON 不证明支持输入数据覆盖；未发现公开的评估启动/查询、取消、隔离重放或故障注入完整契约 |
| 当前 batch 6/7 指标 | 当前 active 指标没有 parent；payload 为最终经济指标与部分 OOS 证据，没有完整 positions/gross/net/cost/equity 序列 | 无法独立重算换手、净收益、Sharpe或母子聚合 |
| factor_value_slice_metrics 抽样 | 日粒度 weighted_universe_factor_value，聚合方式 mean | 日聚合值不是逐 bar 原值；该 mean 也不是母因子聚合规则 |
| pipeline_artifacts | 存在 Parquet 记录；抽样为远端相对 archive 路径，本机不可读；一个当前 metric 样本的 factor/return hash 未命中 artifact checksum | 不能断言全库无原始数据，但当前未建立 batch/manifest 到可读产物的可信关联 |
| factor_replay_case | 1 条 approved 的 OHLCV/open-interest as-of 对齐示例，仅有 fixture、expected 和说明 | expected 不是服务端 actual；没有公式算子执行 Run，不能替代真实计算输出 |
| 发布事件来源 | 找到巡检任务记录，但未找到可关联 eval_batch/publication 的完整发布事务事件序列 | 不能用最终状态证明整个发布过程从未暴露半状态 |

发布语义还需确认：OpenAPI 声明六环境原子发布，batch 6 冻结配置却写 per_factor_incremental；batch 7 是 manual_sql_fixture，不能作为真实执行证明。未擅自调用 publish/rollback，也没有使用组合因子台流程替代因子库 4.0。

继续完成所需的最小信息：计算端源码或正式评估执行/查询接口；当前批次可读取的输入和实际产物及完整版本关联；母子聚合与经济曲线契约；专用隔离范围及允许实际重算/故障实验的授权。已向用户提出这些问题，获得之前不新增虚构协议或恒定 skip 的占位 Case。
