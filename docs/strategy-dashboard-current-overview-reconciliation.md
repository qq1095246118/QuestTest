# 当前数据：接口与数据库来源金额对账

## 已有覆盖与本次补齐

`BV-101 总览“运行中数据”口径` 已在主用例中设计，但历史注册入口没有实现这次页面链路的数值断言。现有 18 个 `portfolio_source_profiles` 验证隔离多账户输入、默认来源和 `population=all`；自然来源风险用例验证单账户风险统计。它们均不替代当前页面的 `population=running / metric_source=prometheus` 汇总对账，因此保留，不重复改造成这组。

原始对账入口：`tests/cases/strategy_dashboard/test_source_current_overview.py`，18 个检查实例。扩展查询入口：`tests/cases/strategy_dashboard/test_source_current_overview_queries.py`，28 个参数化实例。两组合计 46 个实例，分别登记，部分落地 BV-101 的接口数据验收，不声明完成页面渲染验证，不加到固定输入公式场景或 175 个历史主用例数量中。

以下是原始 18 项对账：

| 检查 | 实例数 | 业务断言 |
|---|---:|---|
| 运行账户范围 | 1 | 总览账户数与完整分页账户目录的运行集合一致，不能只保留本地策略绑定账户 |
| 账户明细与 DB 来源值 | 8 | 保证金、未实现、已实现、手续费、资费、总收益率、年化、回撤，逐账户核对来源值、观测时刻及缺失状态 |
| 当前组合金额求和 | 5 | 前五项可加金额从同批次每个运行账户独立 Decimal 求和，保留正负号 |
| 金额完整性 | 1 | 缺源账户不能当真实零；已知小计不能标记 complete |
| 比例完整性 | 3 | 缺少参与账户的比例来源时，不能把单账户比例标成完整组合结果；不伪造组合公式 |

扩展 28 项按真实请求场景登记；一项可能核对 8 个指标，但不会再把这些字段重复算成 8 条新增用例。

| 检查 | 实例数 | 业务断言 |
|---|---:|---|
| 目录分页和筛选 | 6 | 小页完整翻页、运行筛选、停止筛选、交易所筛选、NT-only 单选、本地与 NT-only 混合选择；集合及各行指标与完整目录独立筛选对照，其中运行行额外与同批 DB 源值对账 |
| 单账户分析摘要 | 3 | 已绑定且有来源、未绑定但有来源、来源缺失的本地账户；账户摘要与目录、同批 DB 的 8 项输出对账 |
| 组合账户子集 | 8 | 已绑定单账户、未绑定单账户、两个有数账户、有数与缺源组合、仅缺源账户、仅停止账户、运行与停止混合、选择顺序不变性；核对实际参与集合、5 项金额和缺失状态 |
| 指标投影 | 8 | 逐项单独请求 8 张卡片，与全量请求的同代指标对比；不得因减少请求指标而换来源或改变金额。这 8 项仅验查询不变性，不能代表被比较的总值已独立算对 |
| 日期与时区 | 3 | 当日、近 7 日、相同绝对时刻的 UTC 与北京时间表达；按来源累计语义核对，不强制把当前卡改成区间增量 |

目录中的 `nt:` 账户标识只用于已支持这种 ID 的目录查询。单账户分析和组合子集使用本地数字 ID，不臆造 NT-only 的 account-summary 支持。停止账户筛选验证目录；`population=running` 的组合子集则验证停止账户不会参与当前汇总。

选样中的“有源/缺源”先按保证金来源定位账户，但每项断言仍独立查看该指标的数据库样本，不能据此认定其余七项也全部有源或全部缺失。“已绑定/未绑定”按目录是否存在 `current_assignment` 区分，不等同于再次验证绑定的运行生命周期。已知单账户金额即使标为 `partial`，也必须有原因且数值、观测时间与 DB 相符；不会仅凭 `partial` 判错或跳过数值验收。

指标投影和日期场景核对同代请求结果的稳定性；即使两次接口都返回相同错误，它们也可能通过，因此不得把这 11 项通过解释为金额计算正确。数据库数值正确性由原始 18 项、目录/账户/子集对账负责，两类结果分别呈现。

## 数据链路和独立性

1. 新请求读取 `/admin/quant-accounts`，与当前页面一样使用 `population=all / scope=lifetime / metric_basis=accrual`，不传日期。完整读取所有分页，并锁定分页 snapshot_id；再用每行 actual_status=running 得到运行账户集合。包含 `nt:` 来源账户，不以是否存在本地关系作过滤。
2. 新请求读取 `/quantitative-trading/analytics/summary`，使用 `subject_type=portfolio / population=running / scope=cycle / metric_basis=display / metric_source=prometheus` 及日期。
3. 两路返回的 `meta.fetched_at` 必须相同。跨采集分钟时最多重新采集三轮；只因代次变化重采，不因业务断言失败重试到绿。
4. 只读查询 `quant_collection_batches` 最近 30 条 committed 自然批次的轻量元数据，按 `account_source.raw.fetched_at` 精确定位，再按 ID 读取这一批次的 `account_source`。无匹配明确阻断，禁止用 latest 或旧报告替代。
5. 从 `raw.samples` 提取逐账户输入，独立计算后比对接口。预期从未读取接口 summary 金额，也不使用 summary 返回的账户数来缩小参与集合。

每个扩展查询场景独立读取目录、接口和匹配的数据库批次，并各自保存证据；不要求整个 28 项执行共用一个分钟批次，不复用旧报告替代本轮请求。单项中的对照请求必须匹配同一 `fetched_at`；跨代次只有限重采，业务差异不重试到通过。某项 HTTP 或数据断言失败不能阻止后续查询场景执行。

成员范围来自独立于 summary 的完整授权账户目录，金额来自数据库同批次输入。数据库中的本地 NT 目录同步存在滞后；采集批次也没有完整 enabled 字段，因此本组**不声称所有账户成员都已从数据库独立推导**，也不自行制定跨策略状态取最新的规则。

| API 字段 | 数据库原始指标 | 独立计算 |
|---|---|---|
| margin_equity | account_equity_usdt | 按运行账户求和 |
| unrealized_pnl | account_unrealized_pnl_usdt | 带符号求和 |
| realized_pnl_gross | account_realized_pnl_usdt | 带符号求和；不据 API 名称推定上游毛/净收益 |
| commission_expense | account_total_commission_usdt | 保留来源负号求和 |
| funding_pnl | account_total_funding_fee_usdt | 收款、付款按来源符号相抵 |
| cumulative_return | account_net_value | 每个账户 NAV − 1；不直接聚合比例 |
| annualized_return | account_annualized_return | 每个账户来源到输出的一致性 |
| current_drawdown | account_current_drawdown | 每个账户来源到输出的一致性 |

金额用 Decimal 累加，API 比对容差 `max(1e-8, abs(expected) × 1e-12)`，仅容纳来源浮点尾差，不按 UI 显示两位提前截断。重复且数值/观测时间完全相同的账户级源样本计一次；冲突样本明确失败，不随意挑一条。

若存在缺源账户而接口只返回 null 且没有任何可比较的已知小计，该金额检查报告 `SOURCE_SUBTOTAL_UNAVAILABLE`，不能显示通过；其余成员及完整性检查仍执行。自然样本不足也显式记录 `SAMPLE_GAP`，不伪装成多账户场景通过。

自然场景所需的停止账户、未绑定有数账户、多个交易所、NT-only 账户等不存在时，应保留场景并记录样本缺口；不为了变绿而改选不满足条件的账户，不把离线输入替身当成真实业务覆盖。零余额、正负完全抵消、大额等受控输入已有独立 profile，本组不会仅增加离线断言就宣称这些自然边界已验收。

## 范围边界

- 当前来源累计值不强制按页面日期做差，也不新增“换日期当前卡必须变”的错误断言。
- 年化与回撤本次不重建上游历史路径；组合收益率不套默认来源的 ΣG/ΣD，也不直接平均或累加账户比率。
- 保留已接受的历史缺点补零与部分账户历史曲线差异，不复报。
- 全程仅查询；不写 PG/MySQL，不创建账户、不注入计算结果、不触发重算。
- 离线校验含单账户假汇总、NT-only 账户、停止账户排除、正负相抵、真实零、缺源、重复样本、冲突样本、分页和跨代次；它们验证自动化本身，不算真实业务通过。

## 执行

```bash
cd /Users/wrh/Downloads/索向/接口自动化/QuestTest
QUEST_FORMULA_HTTP_PROXY=http://127.0.0.1:7890 python3 -m pytest \
  tests/cases/strategy_dashboard/test_source_current_overview.py \
  tests/cases/strategy_dashboard/test_source_current_overview_queries.py \
  --live --env test -p no:cacheprovider -q --tb=short \
  --junitxml=reports/quant-current-overview-live.xml
```

代理按本机实际网络选择；不需要代理时去掉该环境变量。默认选择北京时间近 30 个日历日期至本轮查询时刻；可用 `QUEST_OVERVIEW_FROM`、`QUEST_OVERVIEW_TO` 指定带时区的 ISO 日期，不在命令中放凭据。日期只作为页面请求范围，不把动态自然数据改成固定截图数字。

每次成功取数后保存 `reports/quant-current-overview-<uuid>.json`：请求参数、源代次、batch ID、参与/排除账户、每项已知与缺失输入、独立金额小计、接口输出。网络或登录失败写 JUnit setup error，不冒充业务失败。

目录及结果可单独核对：

```bash
python3 -m scripts.report_quant_formula_coverage
python3 -m scripts.report_quant_formula_results \
  --reports reports/quant-current-overview-live.xml \
  --output reports/quant-current-overview-reconciled.json
```

登记报告分别列出原始 18 项和扩展 28 项，登记不等于通过。执行报告用完整模块和测试名称区分两组，保留 `current_overview_results`、`current_overview_query_results` 和两组汇总结果；相同指标或同名方法不得互相覆盖。样本缺口单列为 `current_overview_sample_gaps`，不计为通过或新产品 Bug，未开启 live 的跳过也不得伪装成样本缺口。

固定缺陷中文标题：**当前数据卡片参与汇总的账户范围不一致**。账户数及多项金额失败归同一问题，不拆成多个 Bug。具体实跑结论以该次 JUnit 和 JSON 为准，不以文档登记代表通过。
