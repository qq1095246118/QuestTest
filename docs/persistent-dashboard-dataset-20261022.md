# 三个量化看板模块的持久化测试数据集设计

日期：2026-10-22  
范围：账号管理总览、账号详情、策略详情  
状态：只读审查后的造数设计；本文件不执行数据库写入。

## 1. 执行方式

测试数据只建立一次，后续测试不再创建、更新或删除业务数据。

```text
一次性准备源数据
  → 运行开发侧首次全量拉取/初始化任务
  → 等待策略侧计算、分段和发布完成
  → 记录数据集版本、source watermark、generation、可用账号和策略 ID

每次自动化开始
  → 只读 preflight 检查数据集是否完整、身份是否可见、结果代次是否存在
  → 缺少该 Case 必需数据时只跳过该 Case，并记录具体缺口
  → 数据完整时调用真实 GET 接口
  → 校验响应、source/generation、原始数据和公式预期
```

测试期间不使用 `quant_read_views`、`quant_account_snapshots`、`quant_collection_batches` 直接伪造结果，也不在 Case 的 `finally` 中清理持久化数据。需要重建时通过数据集版本升级或人工执行一次完整重建，不在普通回归中补写历史数据。

## 2. 数据集身份和稳定标识

数据集使用一个可配置的稳定标识，默认建议为 `qta_persist_20261022`，通过 `AUTOMATION_PERSISTENT_DATASET_ID` 覆盖。所有本地和 NT 身份都带该标识，preflight 只按标识查询，不能按名称模糊匹配。

| 对象 | 稳定标识建议 | 用途 |
|---|---|---|
| MySQL 账号显示标识 | `qta_persist_20261022_acct_a`、`qta_persist_20261022_acct_b`、`qta_persist_20261022_acct_c`、`qta_persist_20261022_acct_d` | 账号管理列表、账号详情和策略关联 |
| NT 执行账号 | `qta_persist_20261022_nt_a`、`qta_persist_20261022_nt_b`、`qta_persist_20261022_nt_c`、`qta_persist_20261022_nt_d` | `dim_account`、事实表和本地 `active_nt_account_id` 的稳定连接 |
| 主策略 | `qta_persist_20261022_strategy_main` | 一策略多账号、当前/历史核心指标和资源链路 |
| 切换策略 | `qta_persist_20261022_strategy_alt` | A→B→A 运行区间、历史累计和归属隔离 |
| 对照策略 | `qta_persist_20261022_strategy_other` | 多策略总览、同名/不同身份和排除验证 |
| 数据集来源标识 | `qta_persist_20261022` | `source_key`、`sync_generation`、payload metadata 和 preflight |
| 数据集版本 | `v1` | 结构或场景变更时整体升级为 `v2`，不原地改写旧数据 |

建议为每条事实保留 `dataset_id`、`dataset_version`、`case_tags`、`seed_row_key` 等 metadata。若目标表没有专用列，则放入已有 `payload_json/fields_json/source_payload/metadata_json`，不能写入不受业务识别的伪造结果字段。

## 3. 账号、策略和运行区间

### 3.1 MySQL 业务身份

以下是已在测试库确认的关键列。写入顺序必须先账号、资料，再策略定义、运行记录和 NT 映射。

| 表 | 必需字段 | 数据集要求 |
|---|---|---|
| `quant_accounts` | `id`、`exchange`、`email`、`admin_id`、`api_key`、`secret_key`、`api_description`、`status`、`created_at`、`updated_at` | 4 个专用账号；`api_description` 使用 `qta_persist_20261022_acct_*`；测试凭据为空或使用测试占位；至少覆盖 active、stopped/disabled 读取状态 |
| `quant_account_profiles` | `account_id`、`name`、`business_owner_id`、`created_by`、`status`、`active_nt_account_id`、`version`、`account_code`、`default_risk_config` | `account_id` 与 `active_nt_account_id` 一一对应；`account_code` 不重复；资料状态与账号状态分别保留 |
| `quant_strategy_definitions` | `id`、`code`、`name`、`researcher_id`、`created_by`、`status`、`version` | 主策略、切换策略、对照策略各一个稳定 definition；`code` 不能只靠展示名称识别 |
| `quant_strategy_account_runs` | `id`、`account_id`、`definition_id`、`nt_strategy_id`、`nt_account_id`、`status`、`started_at`、`ended_at`、`end_reason`、`risk_config`、`runtime`、`active_key` | 每个实际运行段一行；同一账号的 A1、B、A2 使用不同 `id` 和时间区间；`active_key` 不能造成历史段冲突 |
| `quant_nt_strategy_accounts` | `nt_strategy_id`、`nt_account_id`、`exchange_id`、`enabled`、`testnet`、`source_updated_at`、`source_status`、`sync_generation`、`synced_at` | 作为 NT 侧当前可见身份；`sync_generation=qta_persist_20261022`；账号与交易所组合稳定 |
| `quant_nt_account_strategy_assignments` | `nt_assignment_id`、`nt_strategy_id`、`nt_account_id`、`exchange_id`、`effective_from`、`effective_to`、`status`、`source_payload`、`source_status`、`sync_generation`、`synced_at` | 与 PG 归属时间线一致；不允许重叠区间；切换点两侧各留一分钟边界样本 |

### 3.2 NT PostgreSQL 归属维度

NT 侧使用正式归属源，不直接向平台结果表补值：

| 表 | 关键字段 | 数据集要求 |
|---|---|---|
| `dim_execution_account` | `account_id`、`exchange_id`、`first_seen_at`、`last_seen_at`、`metadata_json` | 4 个执行账号均可被事实表引用，metadata 写入 dataset marker |
| `dim_account` | `strategy_id`、`account_id`、`exchange_id`、`enabled`、`testnet`、`updated_at` | 当前可见策略-账号组合；对照策略可 disabled，验证过滤与空集合 |
| `account_strategy_assignment` | `assignment_id`、`account_id`、`exchange_id`、`strategy_id`、`migration_id`、`effective_from`、`effective_to`、`opening_snapshot`、`closing_snapshot`、`status`、`created_at`、`updated_at` | A1→B→A2 的唯一归属时间线；`effective_to` 与下一段 `effective_from` 对齐；opening/closing snapshot 保留交接时权益和持仓 |
| `account_strategy_migration` | 迁移身份、源/目标策略、账号、切换时间、状态和交接 payload（以实际 DDL 为准） | 每次切换对应一个 migration；assignment 的 `migration_id` 可回溯到该记录 |

建议时间线（UTC，展示时按 UTC+8）：

| 账号 | 区间 | 策略 | 时间 |
|---|---|---|---|
| A | A1 | `qta_persist_20261022_strategy_main` | 2026-09-01 00:00 至 2026-09-05 12:00 |
| A | B | `qta_persist_20261022_strategy_alt` | 2026-09-05 12:00 至 2026-09-08 18:00 |
| A | A2 | `qta_persist_20261022_strategy_main` | 2026-09-08 18:00 至今/固定结束时间 |
| B | B1 | `qta_persist_20261022_strategy_main` | 2026-09-02 至今 |
| C | C1 | `qta_persist_20261022_strategy_alt` | 2026-09-03 至 2026-09-07 |
| D | D1 | `qta_persist_20261022_strategy_other` | 2026-09-04 至今，作为排除对照 |

A1/B/A2 组合是必须数据；B、C、D 用于一策略多账号、多策略总览、停止区间和空集合边界。所有区间都要至少覆盖 7 个真实运行日的场景数据，另保留少于 7 天的区间以验证年化不可用；两类场景应由不同账号或策略区间承载，不能在同一指标口径中混合。

## 4. 策略侧原始事实

### 4.1 绩效和净值

`fact_performance_sample` 是当前已确认列的运行绩效事实，使用以下字段构造跨模块核心数据；它本身含上游计算字段，写入后必须让开发侧首轮全量任务消费并发布，不能把它当成“接口直接读回即通过”。

| 表 | 关键字段 | 场景数据 |
|---|---|---|
| `fact_performance_sample` | `snapshot_key`、`snapshot_id`、`strategy_id`、`account_id`、`exchange_id`、`snapshot_time` | 每个账号-策略-区间至少一条分钟/周期样本；`snapshot_key` 与 NT source key 稳定且不重复 |
|  | `peak_net_value`、`last_net_value`、`last_equity_usdt`、`last_wallet_balance_usdt` | 设置峰值、当前权益、钱包、权益与浮盈可区分的样本，覆盖回撤恢复和创新高 |
|  | `last_realized_pnl_usdt`、`last_unrealized_pnl_usdt`、`last_total_pnl_usdt` | 正、负、真 0、缺失各至少一组；总收益与策略/账户归属一致 |
|  | `current_drawdown`、`max_drawdown`、`total_funding_fee_usdt`、`total_commission_usdt` | 多峰多谷、手续费/资费正负、费用不重复进入收益 |
|  | `cumulative_net_transfer_usdt`、`performance_initial_capital_usdt`、`capital_base_usdt`、`capital_base_mode` | 初始本金、追加、提取、零净入金、非正本金和资金流边界 |
|  | `performance_start_time_ms`、`performance_transfer_guard_active`、`performance_transfer_guard_reason`、`payload_json`、`fields_json`、`archived_at` | 运行时长、资金保护、缺失/质量状态和修订身份 |
| `fact_nav_sample` | 以实际 DDL 确认的 `snapshot_id/strategy_id/account_id/exchange_id/采样时间/权益或净值/累计收益/回撤` 字段 | 用于分钟峰谷、日展示边界、回撤、波动率和趋势图。该表已有上游计算值，不能只写最终期望再宣称公式通过 |
| `fact_hash_snapshot` | `snapshot_key`、`snapshot_id`、`hash_kind`、`strategy_id`、`account_id`、`exchange_id`、`snapshot_time`、`open_position_count`、`long_exposure_usdt`、`short_exposure_usdt`、`net_exposure_usdt`、`unrealized_pnl_usdt`、`payload_json`、`fields_json` | 保存当前运行状态、持仓摘要和资金/仓位源的可追踪水位；`payload_json` 至少含 `runtime_state`、`ready`、`equity_usdt`、`balance_usdt`、`positions` 等实际源字段 |

### 4.2 持仓

| 表 | 关键字段 | 覆盖场景 |
|---|---|---|
| `fact_position_snapshot_batch` | `snapshot_source`、`redis_key`、`snapshot_id`、`strategy_id`、`account_id`、`exchange_id`、`event_time`、`stream_time`、`signal_id`、`status`、`position_count`、`payload_json`、`fields_json` | 当前批次、空仓、批次状态、重复/迟到和同一 signal 多批次 |
| `fact_position_snapshot` | `snapshot_source`、`snapshot_id`、`symbol`、`position_side`、`strategy_id`、`account_id`、`exchange_id`、`event_time`、`status`、`side`、`signed_qty`、`entry_price`、`avg_entry_price`、`mark_price`、`break_even_price`、`liquidation_price`、`leverage`、`margin_type`、`margin_usdt`、`notional_usdt`、`unrealized_pnl_usdt` | BTC 多仓、BTC 空仓、同合约多空并存、ETH 对照合约、正负浮盈、真实 0、极小价格、逐仓/全仓和已平仓归零 |

主策略至少保留一个多仓和一个空仓；另一个账号保留同名合约但不同 account_id，用来验证账户隔离。`notional_usdt` 必须保留原始精度；页面显示 0.00 的微小成交价格不能在源数据中改成 0。

### 4.3 信号、批次、计划、委托和成交

以下表必须通过父子身份串起来，不能只插孤立列表行：

```text
fact_signal
  → fact_signal_outcome
  → fact_rebalance_plan
  → fact_order_intent_event + fact_order_intent_position_plan
  → fact_order（同一 signal_id/order_id）
  → fact_fill（同一 order_id/venue_order_id，独立 venue_trade_id）
  → fact_position_snapshot / fact_performance_sample
```

| 表 | 关键字段 | 至少覆盖 |
|---|---|---|
| `fact_signal` | `stream_id`、`strategy_id`、`account_id`、`exchange_id`、`event_time`、`signal_id`、`signal_type`、`symbol_count`、`long_count`、`short_count`、`flat_count`、`source`、payload/hash/fields | 成功信号、失败/unknown 信号、多合约、多空计数、重复重放 |
| `fact_signal_outcome` | 以实际 DDL 为准的 signal、批次、状态、完成/失败时间、耗时和 payload 字段 | `succeeded`、`failed`、`unknown`，计划数与实际数不一致，同信号多批次 |
| `fact_rebalance_plan` | 以实际 DDL 为准的 signal、symbol、状态、目标方向、当前方向、weight、quantity、account/strategy | pending→succeeded、部分失败、同 signal 同 symbol 多阶段、0 数量 |
| `fact_order_intent_event` | 事件 ID、signal/plan/intent 身份、account/strategy、时间、事件类型、payload | intent 重放、版本修订、跨账号局部 ID 隔离 |
| `fact_order_intent_position_plan` | intent/plan、symbol、真实仓位、基准仓位、接受变化、方向和精度字段 | 三列独立映射；保留 `accepted_change` 与 real-baseline 不相等的对照 |
| `fact_order` | `stream_id`、`strategy_id`、`account_id`、`exchange_id`、`event_type`、`event_time`、`signal_id`、`order_id`、`symbol`、`side`、`order_type`、`quantity`、`price`、`reference_price`、`trigger_price`、`phase`、`reduce_only`、`reason`、venue_order_id、payload/hash/fields | pending、submitted、partial、filled、cancelled、failed、unknown；买卖方向、订单类型和波次 |
| `fact_fill` | `stream_id`、`strategy_id`、`account_id`、`exchange_id`、`event_type`、`event_time`、`signal_id`、`order_id`、`symbol`、`side`、`order_type`、`last_qty`、`fill_price`、`notional_usdt`、`commission_usdt`、`commission_asset`、`realized_pnl_usdt`、`phase`、`venue_order_id`、`venue_trade_id` | 部分成交、多成交加权均价、开仓/减仓/平仓、负手续费、极小成交价格、失败订单不入收益 |
| `fact_funding_fee` | 以实际 DDL 为准的 account/strategy/exchange、symbol、事件时间、金额、币种、唯一事件身份 | 正/负/零资金费、迟到修订和策略切换边界 |

至少准备一条完整的 `signal→batch/outcome→plan→intent→order→fill→position→performance` 链；另准备一条失败/取消链，确保它不会进入已实现收益。每一条重复事件必须有相同稳定键，验证重放不翻倍。

### 4.4 资金流

`exchange_account_cash_flows` 在 MySQL 中是账号级资金输入：

| 字段 | 设计 |
|---|---|
| `quant_account_id`、`exchange`、`account_email`、`account_type`、`currency` | 与 `quant_accounts` 和 NT account 一一映射；默认 futures/USDT |
| `flow_type`、`amount_usdt`、`external_flow_id`、`raw_type` | 充值、提取、调整、待确认各一条；`external_flow_id` 幂等且不复用 |
| `occurred_at`、`is_backfill`、`raw_payload` | 覆盖策略切换前后、UTC+8 日边界、迟到/回补和无效状态 |

资金流只作为账户本金和分段归属的输入，不把 `quant_account_snapshots` 的最终值当作源。待确认流水不能进入已确认本金；同一 external_flow_id 重放不能翻倍。

## 5. 场景覆盖矩阵

| 数据场景 | 主账号/策略 | 账号管理 | 账号详情 | 策略详情 |
|---|---|---|---|---|
| 单账号、正收益 | B + main | 列表金额、总收益率 | 16 核心指标、曲线 | 当前/历史核心指标 |
| 多账号正负收益汇总 | A、B、C | 全量聚合、筛选、分页 | 各账号独立详情 | main 多账号归属 |
| 一账号多策略 A→B→A | A + main/alt/main | 账号当前策略和历史关联 | 当前区间/历史累计、分段收益 | 当前/历史、切换边界 |
| 一策略多账号 | B、C + main | 全账号总览 | 两个账号分别核对 | 策略汇总、账号表 |
| 多交易所/同名合约 | D 或交换所对照 | exchange 筛选 | account/exchange 隔离 | 跨所聚合与显式 exchange |
| 正、负、真实 0、缺失 | A/B/C 各分配一个字段 | 状态和金额分离 | null/0/partial/unavailable | 16 指标状态 |
| 小于 7 天、恰好 7 天、超过 7 天 | C、B、A1 | 年化和 Calmar 状态 | 年化边界 | 当前/历史运行日 |
| 分钟峰谷、日显示 | A1/B1 | 总回撤/波动率 | 回撤、Sharpe、Sortino、曲线 | 风险和曲线 |
| 持仓多空/平仓 | A、B | 当前仓位和总览 | N13/N01 | positions |
| 成交链成功/失败/部分成交 | A | 胜率、盈亏比 | N18/N19/核心指标 | signals/orders/fills |
| 资金流和费用资费 | B、C | 收益率和本金 | 核心金额、曲线 | 费用/资费归属 |
| 重复、迟到、修订、跨代 | 所有需要的区间 | snapshot/generation 状态 | 数据质量状态 | 资源去重和代次 |
| 空策略/空账号/停止区间 | D、C | 空集合与状态 | stopped/无持仓 | 历史保留、当前排除 |

## 6. 一次性初始化顺序

1. 只读检查目标环境、数据集版本和已存在 marker；若同版本数据已存在，禁止重复插入。
2. 在 NT 侧准备 `dim_execution_account`、`dim_account`、策略归属和 migration；在 MySQL 准备账号、profile、strategy definitions、runs 和 NT 映射。
3. 写入全部历史 assignment、分钟绩效/NAV、持仓批次、信号、订单、成交、资金费和资金流；所有父子关系、source timestamp、稳定键和 payload metadata 一次提交。
4. 运行开发侧首次全量拉取/初始化任务，使历史数据进入真实计算和发布链；记录任务 run id、source watermark、generation、formula version 和完成时间。
5. 只读等待并验证三个模块的真实 GET 接口均能看到该数据集；若只有源表而无发布结果，数据集状态为 `not_ready`，不进入普通回归。
6. 保存 manifest（账号、策略、区间、源表行数、关键边界、预期值和生成代次），之后不允许普通 Case 改写或清除。

## 7. 每次测试前 preflight

每条 Case 声明依赖，例如 `accounts=[A,B]`、`strategies=[main]`、`intervals=[A1,B1]`、`resources=[fills,positions]`、`min_generation`。preflight 只读检查：

- MySQL 账号、profile、strategy definition/run、NT 映射存在且身份相互匹配；
- PG `dim_account`、assignment、事实表均存在所需 `dataset_id/case_tag`；
- 归属区间没有重叠，切换边界和时间窗样本存在；
- 绩效、NAV、持仓、订单、成交父子链可追溯，源行数和稳定键与 manifest 一致；
- 最近发布的 generation/source watermark 不早于 manifest 要求，且接口读取能返回 `mock=false`、正确 subject 和数据状态；
- 账号/策略未被删除、禁用或改绑；若改绑导致 Case 语义变化，跳过该 Case，不尝试修复数据。

缺口必须输出 `PERSISTENT_DATASET_MISSING`、`PERSISTENT_DATASET_IDENTITY_MISMATCH`、`PERSISTENT_DATASET_NOT_PUBLISHED` 或 `PERSISTENT_DATASET_SOURCE_INCOMPLETE`，并带缺少的表/稳定键。跳过只作用于当前 Case，不能把整个模块静默跳过，也不能把跳过算通过。

## 8. 清理策略

普通自动化不清理持久化数据；数据清理只在人工确认、数据集版本退役或完整重建时执行。清理必须按 `dataset_id + owner + stable key` 反向依赖删除，顺序为：读取页/派生缓存（如确实属于数据集）→ 事实子表 → 事实父表 → assignment/migration → strategy runs/definitions → profiles/accounts。共享 generation、Redis key 或非数据集对象不能整批删除。删除后用同一 manifest 做零残留检查，并将数据集状态标记为 `retired`。

## 9. 仍需开发侧确认的入口

- 首次全量拉取/初始化任务的正式命令、完成标志和可重放方式；
- PG 侧 `fact_nav_sample`、`fact_signal_outcome`、`fact_rebalance_plan`、`fact_funding_fee`、`fact_order_intent_*` 的实际 DDL 和必需非空字段；
- 写入源事实后，哪一个 worker/consumer 生成 `quant_segmented_performance_generations`、曲线和 API 可见 snapshot；
- 当前接口是否按 `metric_source` 选择 Prometheus、分钟事实或分段结果，及每个字段的实际来源；
- 发布后的 generation 是否允许固定读取，及历史修订的重放/水位规则。

没有这些确认前，不能把“源表插入成功”当成三模块可执行；preflight 应报告未发布或来源未绑定并跳过对应 Case。
