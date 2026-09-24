# 分润平台公式验证：已核实数据路径与当前缺口

调查日期：2026-09-23。范围只含测试环境分润平台的原始数据准备、真实计算和结果查询路径。前半段为只读调查，后续仅写入并清理了专用公式输入；未修改既有业务账户，未调用交易所历史回补、启停、分润、审核或清算接口。

## 当前结论（2026-09-24 汇总）

现已接通两种真实计算路径：原始分钟来源，以及非分钟 `quant_read_views.payload` 原始业务视图。原始历史权益、G/C/F/D 起终快照、首个周期基准、逐笔已实现事件、逐笔收款登记和账本输入，均已有隔离造数后查询真实服务的用例。组合账户金额、累计收益率、持仓聚合和部分风险公式也已执行；实际差异和未接通项见 [本轮结果](strategy-dashboard-formula-expansion-state.md)。下文“仅12条”和基线不可用的结论属于早期探查阶段，不代表最新状态。

真实入口统一登记在 `tests/strategy_dashboard_formula_routes.py`，完整逐场景输入/预期通过 `python3 -m scripts.report_quant_formula_coverage --output reports/quant-formula-coverage.json --markdown reports/quant-formula-scenarios.md` 生成。此统计只判断适配状态，不推断执行通过。

当前仍有明确边界：E−U 钱包推导分支尚未接通；默认历史引擎不支持 Sortino，现仅自然来源核对与本地固定场景；Gclear 清算基准尚未证明被受控输入消费；资金事件到 D、成交到 G 的上游聚合不在累计端点测试中；锁单、确认收款、清算动作不能由统计读取替代。显式 Prometheus 查询会改用全局监控来源，没有将其当作自有输入的验证路径。

## 早期调查结论（保留探查历史）

当前已经完成部分真实公式注入验收：通过测试 MySQL 写入独立账户的原始分钟持仓和资金事实，再调用已部署的 `/quantitative-trading/analytics/summary`，后端重新计算持仓数量、名义金额、浮盈、保证金求和及 G/D 累计收益率。持仓 6 条、累计收益率 6 条已取得真实通过证据。这里是“数据库准备采集后的输入 → 真实 API 计算”，不是“HTTP 接口导入任意历史原始事实 → 采集器同步”的全链路。通用 HTTP fixture 执行器仍是待绑定协议，不能与已经跑通的数据库路径混为一谈。

原始累计金额属于 M03 的输入，但属于交易所成交的上游汇总结果；本次没有证明从逐笔成交生成 G、从资金事件生成 D 的采集链。当前可执行数量与入口以 `python3 -m scripts.report_quant_formula_coverage` 输出为准。

## 累计收益率与未接通路径

- `FM-RETURN-001～006` 分别验证正收益、负收益、真零、零净入金、负净入金、净入金缺失。源快照不包含 `cumulative_return`；实际接口以 `exchange_cumulative_realized_latest_net_deposit_v1` 计算 G/D，并逐条核实 snapshot、generation、账户身份和 `mock=false`。
- `FM-RETURN-008` 还要求 Gbase/Gclear 变化不影响累计收益率。曾经的七例报告没有实际绑定这两个基准，因此已将第八号场景恢复为未适配，不能从重复 G/D 算术外推其覆盖。
- M01 钱包：仅注入 E=12240、U=250 并删除 W 后，当前分钟 source 路径返回 W=null / `SOURCE_FIELD_UNAVAILABLE`。这条路径读取同源钱包值，没有触达 Prometheus 的 E−U 推导；不计钱包计算覆盖，不据此认定产品 Bug。
- M02 与历史风险：额外写入同源历史基础快照并校正字段时点后，当前分钟查询仍返回 `BASELINE_UNAVAILABLE`。缺少实际基线选择和完整历史输入消费绑定，不由本地算法补上结果。
- M04、M14～M17：已定位真实周期、政策、基准和批准公式的表关系；仍须证明真实查询消费这些输入并输出派生结果。静态 G/Gbase/D/Gclear 算例不证明锁单、政策冻结、收款或清算动作。

具体探针和清理证据见 `reports/quant-formula-development-20260923/financial-baseline-probe.json`、`profit-share-source-status.md` 及 `reports/quant-wallet-source-probe-qfi_a2fb9c294e2e4ab1a39136045d376252.json`。本次也独立查询了既有账户在造数前、中、后的默认接口，三次均保留原 generation；记录在 `default-query-isolation.json`，只代表这次实际选择行为，不代替全部后台选择代码审计。

## 2026-09-23 补充：已执行的持仓公式路径

正式用例：`tests/cases/strategy_dashboard/test_source_position_aggregation.py`。沿用已有场景 ID，不新增业务主 Case 数量：

| 场景 | 原始输入 | 真实摘要结果 |
| --- | --- | --- |
| FM-POSITION-001 | 同一合约多头名义 200、空头 -110，另外一条零持仓 | 持仓数 2、总名义 310 |
| FM-POSITION-005 | 数量 2、名义金额 null | 持仓数 1、总名义 unavailable/null |
| FM-POSFIELDS-001 | 浮盈 100/-40，保证金 30/20 | 浮盈 60、保证金 50 |
| FM-POSFIELDS-002 | 浮盈 100、保证金 null | 浮盈 100、保证金 unavailable/null |
| FM-POSFIELDS-004 | 浮盈 null、保证金 30 | 浮盈 unavailable/null、保证金 30 |
| FM-POSFIELDS-005 | 完整持仓空数组 | 浮盈 0、保证金 0 |

本次执行 6 passed；证据目录：`reports/quant-raw-position-3d7d6835dbc94849bb50967c3fdb775f/`。每条保存独立 fixture、真实账户 ID、原始输入、服务端响应、固定预期及清理验证。账户资金快照特意采用浮盈 77、保证金 456，区别于持仓期望，避免把快照预存汇总值读回误算成公式通过。

具体数据路径：

1. 创建仅本次使用的 `quant_accounts` / `quant_account_profiles` 身份，exchange=`questtest_fixture`，无交易所凭据、NT 映射、策略绑定、分润政策或周期。
2. `quant_account_snapshots` 提供账户公式输入；`quant_collection_batches.current_data.account_positions` 保存逐笔持仓输入，不保存名义合计、持仓数量或比率输出。
3. 自有 `quant_read_views` 使用当前实际版本 1 结构，通过 `batch_id` / `account_snapshot_ids` 精确引用输入；`business` 只提供本次账户身份。
4. 查询 `GET /quantitative-trading/analytics/summary?subject_type=account&subject_id=<id>&scope=lifetime&metric_basis=accrual&snapshot_id=<fixture>`，验证响应绑定同一身份和 generation，再断言 `exposure_summary`。
5. 按 UUID、身份和所有者精确清理 view、page、batch、snapshot、profile、account，随后独立查询六张表，确认本次资源残留为 0。HTTP 或断言失败同样清理并保存证据。

真实 loader 仅支持 `quant-minute-test` 命名空间；因此批次 cutoff、created_at、published_at、updated_at 均放到过去，generation=1，且每次检查按 cutoff/created_at/published_at/generation 四种排序得到的最新批次均不是本次 UUID。此检查不等同于后台代码审计。原始快照 observed_at 使用当前时间，满足真实公式来源新鲜度要求；并非验证历史采集、历史窗口或旧快照优先级。

未使用测试侧去重、取最新、过滤零持仓或先计算合计。多时间点、复投去重、跨账户、混币、同符号不同市场、不同保证金类型尚未接到这个单账户当前分钟路径，仍由目录中的原场景明确保留待接入，不能按本次 6 passed 外推。

## 本次实查与历史证据的时间边界

- 2026-09-23 07:00:58 UTC，重新连接测试 PostgreSQL `nt_execution`，服务端确认 `transaction_read_only=on`。只读查询了六张事实表的列、类型、非空约束、默认值和可见触发器。
- 同日重新连接测试 MySQL `factor_db`，连接使用项目 `read_only=True` 设置。只读查询了相关表目录、部分列结构和 `quant_calculation_formulas`。
- API 来源映射使用 2026-09-22 已保存的真实响应，不宣称这些来源字段已经在 2026-09-23 再次请求验收。响应中的指标观察时间约为 2026-09-22 12:22～12:24 UTC，各指标存在不同的来源和窗口。
- Lark PostgreSQL 表说明使用 2026-09-21 从用户已打开页面保存的导出，标题为《NT Execution PostgreSQL 数据库表结构说明》。它解释来源语义；实际列是否存在以本次只读查库为准。

## 已证明的路径

历史独立核对按 `venue_trade_id` 将 PostgreSQL `fact_fill` 与真实 `/quantitative-trading/trading/fills` 返回逐笔对应，验证数量、价格、名义金额、手续费、方向、事件时间及账号映射。它证明明细读取和字段搬运，不自动证明累计收益、收益率、回撤或分润公式重算。

可复核证据：

- [数据库核对说明](../reports/quant-ui-regression-20260922-201519/db-audit.md)
- [PostgreSQL 查询和结构](../reports/quant-ui-regression-20260922-201519/source-pg-baseline.json)
- [MySQL 查询和结构](../reports/quant-ui-regression-20260922-201519/platform-db-baseline.json)
- [逐字段对账结果](../reports/quant-ui-regression-20260922-201519/db-api-field-reconciliation.json)

## PostgreSQL 输入层级

| 表 | 已核实内容 | 对公式验收的限制 |
| --- | --- | --- |
| `fact_fill` | 成交数量、价格、手续费、已实现盈亏、订单/信号身份 | 可作为成交聚合输入；仍须确认目标计算实际读取此表 |
| `fact_funding_fee` | 逐笔带符号资金费及币种、时间 | 可作为资金费聚合输入；不能默认它会更新其他累计状态 |
| `fact_position_snapshot_batch` / `fact_position_snapshot` | 完整批次和逐合约方向的持仓事实 | 可核对数量、名义金额和聚合，但须确认目标查询采用哪个快照来源 |
| `fact_nav_sample` | 权益、净值、累计收益、回撤采样 | 来源是 `nt:nav_samples:{strategy}:{account}` Stream，已含上游计算值；写入净值或回撤后读回，不证明这些指标被重新计算 |
| `fact_performance_sample` | 累计收益、费用、本金基准、峰值、净值和回撤状态 | 来源是 `nt:performance:{strategy}:{account}` Hash 周期快照，不能把预先算好的结果当作原始输入 |

本次在 `information_schema.triggers` 中未查到上述六张表的触发器。这只说明没有找到这些表的数据库触发计算机制，不能据此断言不存在后台应用任务或其他消费路径。

PG 表说明将该库定义为执行事实归档域；Redis/交易所产生的事实归档到相应表。说明没有提供“向 `fact_fill` 插入记录后自动重建 `fact_performance_sample`”的调用契约。

## V2 输出存在多个计算来源

2026-09-22 的账户 summary 响应明确返回以下来源：

| 指标 | 响应 source | 影响 |
| --- | --- | --- |
| 累计/年化收益率、当前/最大回撤、Sharpe | `prometheus` | 单独向 PG 插入成交，不足以证明该指标会消费这些输入 |
| 年化波动、Sortino | `prometheus_daily_nav` | 需要确认日净值的来源、日界线和同步方式 |
| 胜率、盈亏比 | `binance_realized_income` | 需要确认收入事件读取路径，不能替换为任意 PG 成交盈亏样本 |
| 仓位率 | `minute_positions` | 需要确认分钟持仓输入和快照更新方式 |

证据：[账户 summary 原始响应](../reports/quant-ui-regression-20260922-201519/api-independent-account-2011-current-summary.json)。不同来源不能无说明地混用；造数执行器必须绑定实际消费来源。

## MySQL 本次确认的公式配置

`quant_calculation_formulas` 本次只读返回一条 `approved` 记录：`account_realized_gross_settled_capital_v1`，类型为 `profit_sharing`。其配置规定：按账户累计毛已实现收益计费，本金使用最近净入金加最近清算毛收益，不包含手续费和资金费，金额按 `HALF_UP` 保留 8 位。

库中也存在 `quant_trading_events`、`quant_strategy_performance_samples`、`quant_curve_points_minute`、`quant_curve_points_rollup`、`quant_account_snapshots` 和 `quant_read_views`。表存在不等于已确认写入与计算契约；直接向最终绩效、曲线、快照或账单写入期望值再查询，只能证明读取/映射，不能算原始输入公式验收。

## 尚需绑定的最小能力

以下针对尚未接入的公式族和通用 HTTP 造数路径；上述持仓子集已使用数据库输入方案实际执行，不再按“全部无入口”统计。

1. 测试侧原始数据进入真实计算的入口：已存在的 fixture 接口、隔离上游响应，或明确由计算实际消费的原始事实表。
2. 每个公式族对应的 `metric_source`、身份映射、时间窗口和完成条件。
3. 写入后如何被平台消费：请求内计算、指定后台同步任务，或已存在的内部计算接口；不能臆造重算端点。
4. 专用测试账号/策略，或明确的创建与映射接口，避免把同名对象当成同一身份。
5. 本次测试资源的清理或恢复动作，以及任务超时后的查询恢复方式。

公开 OpenAPI 尚未声明任意合成账户、持仓、成交和资金记录的 fixture 写入接口。已有历史回补接口会读取交易所数据，不是合成数据导入接口。本地目录检查未找到量化后端源码，因此尚不能从代码确认内部测试入口。

缺入口时，真实用例应报告阻断；离线公式预期、客户端编排自测和契约检查必须单独统计，不能称为真实公式闭环通过。
