# 分润平台公式场景覆盖依据与扩展清单

本文件是公式验收范围和自动化数据设计依据，不把清单条目计入正式用例数，也不把文档检查通过当作真实业务通过。现有 175 条主用例及其稳定中文标题保持不变；`CALC-001～050` 与 `docs/strategy-dashboard-follow-up-cases.md` 的预留编号不重新占用。

2026-09-23 已定位并只读核对最新版 16 页《量化交易 V2 业务与计算总览》，正文更新时间 2026-09-19，当前位于 `/Users/wrh/.Trash/量化交易 V2 业务与计算总览.pdf`（没有移动或恢复用户文件）。公式采用该版第 5～8 页，窗口/初始化/财务采用第 8～12 页，TBD 采用第 13～15 页。同时对照根目录 2026-09-18 的旧 43 页稿、`design/02-calculation-cases.md`、`design/03-alerts-and-settlement.md` 和 follow-up 清单。以下 `confirmed` 指最新版给出明确业务算式，不声称接口已经实现或通过；`contract_pending` 指只能按已签认配置选择唯一算法，不能猜一个常用公式；`workflow` 指必须发生真实业务动作、查询动作后结果才能验收。

所有可执行场景的有效流程均为“原始来源输入 → 真实接入/计算 → 查询平台结果 → 独立预期断言”。只向测试服务提交已算好的输出、只运行 Decimal 算式或只检查元数据都不证明平台公式正确。每条场景还需保存真实写入收据、隔离对象、查询窗口、计算后输出及清理结果。缺少写入或业务操作能力时应明确阻塞，不能伪造已注入。

用户已接受图表缺历史补零、失败空表、刷新失败保留旧展示和一定范围的极小数格式。下表只检查这些展示未反向污染计算原值、财务事实和派生结果，不重新将已接受行为列为缺陷。

## 本次版本核对的实质变化

- 最新版 M05 使用“同口径收益率/运行天数×365”，旧稿曾直接写输入 M03。账户累计年化可以使用 M03，但不能把所有策略或指定区间年化无条件绑定账户 G/D；分子和天数必须同窗，T01/T02 未被自动批准。
- 最新版 M06 明确至少 2 个完整日收益（3 个相容边界）；7 天门槛属于 M05，M07因依赖 M05 也受限制。不将旧稿“数学算例不解除运行时长要求”的宽泛措辞扩展为 M06 的额外7天门槛。
- 最新版明确图表允许缺点补0视觉占位，同时不回流到公式；用户后来进一步接受了几类展示行为，以用户确认优先。原 CALC-047“缺口必须断开或明确标识”不应继续作为页面失败依据。
- 最新版 M20 增加持仓浮盈求和、同类型已用保证金求和；M22仅固定兼容单位的数量加权均价，旧稿合约精度 HALF_UP 不能无技术契约继续强制。M17 的应收8位 HALF_UP 保持不变。
- 最新版补齐无历史清算时的当前实现初始化、未达标周期不因月末重置/不出零账单、清算不是再次扣款、跨月实际到账与确认截断，以及更正流程未完整支持的边界。这些应做 workflow，不伪造成单一算式测试。
- T01～T17 在新版仍未签认；Sortino、胜率样本、盈亏比、净敞口、保证金率、健康度端点、投资净值及基准不能因为新版给了候选数字就转成正式数值断言。

## M01～M30 逐项边界与独立预期

| 业务 family / 依据 | 状态与已有主用例 | 可落地的原始输入和独立预期 | 还要展开的区别性场景 |
| --- | --- | --- | --- |
| `wallet_balance` / M01 | confirmed；CALC-001、004、048 | 同一时点 E=10300、U=300 → W=10000；E=9700、U=-300 → W=10000；E=0、U=0 → W=0；E 或 U 缺失 → 推导 W=null | 正负 U、实际零值、缺 E/缺 U、金额精度、不同账户和水位隔离。不能拿 D+G 代替本式；同源直接钱包值只作对账 |
| `window_net_trading_pnl` / M02 | confirmed；CALC-005、009、010 | G:500→1100、C:50→80、F:-10→-30 → 600-30-20=550；收到资金费 +20 → 590；G减少100、费用30、资金费-20 → -150 | 零/正/负收益、费用为零、付款/收款资金费、任一端点缺失、入金/U改变但结果不变、边界事件、累计计数器重置不可直接当负费用 |
| `cumulative_account_return` / M03 | confirmed；CALC-006～008、043 | G=600、D=10000 → .06；G=-600 → -.06；G=0 且 D>0 → 0；D=0/-1 或缺失 → null | 追加入金只改 D，不能改 G；多策略共享同一账户不重复本金；不能将来源策略 TWR 或净值减一冒充账户 G/D |
| `post_clear_return` / M04 | confirmed；CALC-033、034、041 | G=800、Gclear=600、D=10000 → 200/10600；G=Gclear → 0；G=500 → -100/10600；D+Gclear≤0 → null | Gclear缺失不默认0；无清算但已明确初始化为0与基准丢失分开；负Gclear且分母仍正；清算不改累计G |
| `annualized_same_scope_return` / M05 | confirmed 算式；分子对象/窗口、天数选择 contract_pending T01/T02；CALC-011～013 | 同窗收益率.10、经确认运行10天→3.65；账户累计口径G/D=.06且已确认30天→.73；6.99天→null；0收益且≥7天→0 | 最新版已改为“同口径收益率”，不得无条件将所有年化绑定M03或ΔG；负收益、恰好7天两侧、天数缺失/0、30/365/366天分母；年化常数仍365，不是复利 |
| `daily_equity_returns` / M06 | confirmed 无现金流且完整边界；现金流/日界线 contract_pending T02/T16；CALC-014 | 原始权益 100→101→99.99 → [.01,-.01]；100→100→100 → [0,0]；前一点≤0 → 对应日不可计算 | 输入必须是权益点而非已经给出的returns；缺中间日、未闭合当日、乱序/重复边界、现金流日不能直接套比值 |
| `annualized_volatility` / M06 | confirmed；CALC-015、045 | 上述2个日收益样本：样本方差 .0002，年化波动=sqrt(.0002×365)=sqrt(.073)；只有1个日收益→null；[.01,.01]→0 | 最新版明确2个完整日收益/3个相容边界可算M06；不把M05的7天门槛额外加到单独M06。Sharpe因M05不足7天仍不可算；其余覆盖正负交替、近似收益、缺日/现金流、ddof=1 |
| `sharpe` / M07 | confirmed；CALC-016 | 同窗口年化收益 .20、年化波动 .10 → 2；-.20/.10→-2；0/.10→0；波动0或任项缺失→null | 从原始G/D、运行日和权益路径完整推导；不能用 mean(return)/std、非年化累计收益/波动替代；窗口不同不可算 |
| `calmar` / M08 | confirmed；CALC-017 | 同窗口年化收益 .20、最大回撤 .10 → 2；-.20/.10→-2；回撤0/缺失→null | 正负/零年化、回撤正负存储均按abs分母、窗口不同、路径缺峰值；不能用当前回撤替代最大回撤 |
| `current_drawdown` / M09 | confirmed 完整同窗口路径；CALC-018、020 | 100→120→90→110 → 当前10/120；100→120→120→110 → 同值；单调上涨当前0 | 相同峰值、新峰值、恢复、同值、峰值≤0、路径缺起点；缺失绘图补0不能变成真实谷值 |
| `max_drawdown` / M10 | confirmed；CALC-019～021 | 100→120→90→110 → .25；100→80→120 → .20；100→120→80 → 1/3；恢复至120仍保持旧最大值 | 顺序交换、首点即峰值、重复峰值、多段回撤、100%回撤、抽稀隐藏谷值、缺历史；不能只算全局max/min |
| `strategy_contribution` / M11 | confirmed；CALC-022～024、043 | 策略净收益 [100,-50,0] → [2/3,-1/3,0]；[100,-100]→[.5,-.5]；全0→null | 全正、全负、正负抵消、零与缺失不同、费用归属缺失、Top6与完整分母、跨账户同策略归属。Top6排序/tie-breaker未定义不能强加 |
| `plan_match_rate` / M12 | confirmed 聚合；目标身份和终态 contract_pending T15；CALC-025、026 | 两批 `(matched=0,total=1)`、`(99,99)` → .99；全部0目标→null；任一批total未知→不能完整率 | 分子为0/等于分母、分子>分母为源异常；100小批与1大批不能平均；重复批次、替换/取消需正式目标生效规则 |
| `risk_health_weighted_score` / M13 | confirmed 权重和缺项；分项映射 contract_pending T03/T04/T05；CALC-027、028 | 已批准四项分数 [80,90,100,95] → .4×80+.2×90+.2×100+.2×95=89；任意缺项→null | 分数0/100、单项缺失/多项缺失、不重归一；不能把指标值直接套 `(1-x)*100` 或自行签认候选端点 |
| `eligible_profit` / M14 | confirmed；CALC-035、037、043 | G=1100、Gbase=500 →600；G=500→0；G=400→-100；Gbase缺失→null | 费用/F/U变化不改变计费毛收益；负收益不转正；仅策略归因变化不改账户收益；是否亏损结转待T11 |
| `capital_base` / M15 | confirmed；CALC-036 | D=10000、Gclear=1300→11300；Gclear=-500→9500；D或Gclear缺失→null | 分母正/零/负、入出金改变最新D、Gbase不参与本金、同账户多策略只算一遍 |
| `cycle_return_rate` / M16 | confirmed；CALC-035、036 | G=1400、Gbase=1100、D=10000、Gclear=1300 →300/11300；本金≤0→null | 计费收益正/零/负、缺基准、清算基准更新而Gbase不变、同窗口对账 |
| `receivable_amount` / M17 | confirmed；CALC-038、SET-049 | (1100-500)×.20=120.00000000；eligible=.000000025、rate=.20 → HALF_UP(.000000005,8)=.00000001；.000000024×.20→0E-8 | ROUND_HALF_UP的低于/等于/高于半单位、正大数、高精度、比例与门槛分离、页面六位不反向改变8位入账。政策允许比例范围需契约 |
| `billing_eligibility` / M18 | confirmed 数值门禁；精确证据规则/动作 workflow；CALC-039、040、AL-001～007 | enabled=true、证据合格、Gbase=500、D=10000、Gclear=0、gate=.05；G=999.9999/1000/1000.0001→false/true/true | 单独关闭enabled、利润≤0、本金≤0、null、缺精确证据、陈旧/未来/非支持币种；输入精度高于展示门槛，不能按显示5.00%锁单 |
| `net_deposit_and_cash_flow` / M19 | confirmed 同币种/正式分类；跨币/内部转账 contract_pending T10；CALC-002、003、010 | 合格入金12000、出金2000→10000；出金13000→-1000；同一窗口流量按真实事件时间求和 | 转入/转出/零、重复event、金额相同但event不同、start包含/end排除、同账户局部ID与跨账户作用域、内部互转组合净0但分类需契约 |
| `positions_count_gross_notional` / M20 | confirmed 来源已有名义金额；数量×价仅线性合约；CALC-029 | 两个有效仓位notional=600/-400 → count2、gross1000；浮盈20/-5→15；同类已用保证金100/200→300；数量0记录不计 | 多空同合约不能因相抵当空仓；不同保证金类型不得合计；重复仓位只保留同一事实；逆向合约不能强用qty×price。净敞口和保证金率属于T03/T04 |
| `order_count_completion` / M21 | confirmed 唯一订单最新状态；终态契约T15；CALC-030 | 4唯一订单最新状态为filled/filled/partial/cancelled→count4、completion .5；空集合→null | 多事件仍1订单、乱序时间取最新、全完成/全未完成、部分成交不等于完成、同订单跨账户作用域 |
| `fill_vwap` / M22 | confirmed 同订单兼容数量单位；输出精度按技术契约；CALC-031 | 成交1@100、2@110→320/3；2@100、1@110→310/3；任一有效成交缺失或数量和0→null | 最新版只固定数量加权公式，未继续写“合约HALF_UP”；不能将旧版精度擅加为新断言。单笔、多笔不等量、部分成交、分页、重复fill、跨合约与逆向数量单位分开 |
| `execution_latency_latest_times` / M23 | confirmed 同链相减/独立max；链起止事件 contract_pending T15；CALC-032 | 链A触发10:00:00完成10:00:12→12秒；链B最近触发10:01但未完成，A延迟仍12；0秒有效 | 完成缺失/早于触发→null，跨链交错、失败终态不等于成功、重复/乱序事件；最近触发与最近完成不能直接相减 |
| `receivable_received_outstanding` / M24 | confirmed 聚合；真实收款 workflow；SET-013～024 | 唯一账单应收100、confirmed付款40、pending付款60→应收100/实收40/待收60 | 重复账单关联、多个确认付款、拒绝/待审不计、确认时间>cutoff不计、不同账户/币种不混合；超收按现有确认契约拒绝而非静默截断 |
| `collection_progress` / M25 | confirmed；SET-015～018 | 同账单集合 confirmed40/receivable100→.4；0/100→0；100/100→1；无应收→null/不适用 | 多页账单先合计再相除、不平均各账单进度；待审不计；空集合不能显示100% |
| `latest_received_at` / M26 | confirmed；GAP-001 | confirmed p1实际到账9:00审核12:00；confirmed p2实际到账10:00审核11:00 →10:00；pending p3到账13:00忽略 | 无确认收款→null、相同到账时点、跨月、反序审核、同账单集合过滤；不取审核/登记时间 |
| `bill_count_and_overdue` / M27 | confirmed 去重count；逾期contract_pending T13；GAP-002 | 已确认due=10:00、待收60；cutoff=10:00→不逾期，11:00→逾期3600秒；待收0→不逾期 | 到期起算点未签认不擅自造72小时；无due→未知/不适用；多次通知不擅自顺延；同账单分页/多笔付款仍只计1账单 |
| `grouped_accounts_and_funds` / M28 | confirmed 物理账户去重；全集/组合比率contract_pending T14；CALC-043、050 | 账户A权益1000绑定两策略→count1、equity1000；加账户B权益2000→count2、3000 | running/stopped/unknown/mixed、重复绑定、多页、全部与运行中集合、未知金额不当0；比率不能简单平均，未签认组合算法不新增公式 |
| `realized_gross_nav` / M29 | confirmed；GAP-003 | M03=.06→lifetime净值1.06；G=800、Gclear=600、D=10000→display净值1+200/10600 | 零收益净值1、亏损低于1、非正D/缺Gclear→null；清算只重置展示净值到1、不重置历史净值；不是现金流调整TWR |
| `performance_and_benchmark_nav` / M30 | contract_pending T02/T09/T16；GAP-003、006 | 无现金流、明确起止时点时 E=100→110 的固定本金候选为1.10；但是否采用该口径必须有profile；基准只可用指定已收盘价比 | 缺起点、非正起点、未收盘价、现金流、不同日切/桶、基准资产、时区；未确认不直接用候选公式报Bug |

## M 表之外需要明确保留的公式族

这些不是同一公式复制若干“正/负/零”就算覆盖；每个族所需原始事实和可判定范围不同。

| family | 当前结论 | 具体输入与可写的唯一断言 |
| --- | --- | --- |
| `win_rate` | contract_pending T06；GAP-004/拟CALC-055 | 同一闭合交易拆成两笔成交、另有平手/未平仓/费用改变正负的样本。先固定按成交、订单还是完整交易及平手分母；未定前只能保证不混对象，不能固定2/3或2/4 |
| `average_win_loss_ratio` | contract_pending T07；GAP-004/拟CALC-056 | PnL=[100,20,-10,-50] 时平均盈亏比=60/30=2；总利润因子=120/60=2，此样本分不出两者，需换[100,20,-10]：均值比=60/10=6、总因子=120/10=12。正式选择后固定断言；全盈/空样本不返回Infinity |
| `sortino` | contract_pending T08；GAP-004/拟CALC-057 | 日收益[.10,-.02,0]可以区分全样本下行偏差sqrt(.0004/3)与仅负样本.02；目标收益0与非0不同。未签MAR、样本分母、年化方式前不能拿Sharpe替代 |
| `net_exposure` | contract_pending T03；GAP-004/拟CALC-058 | BTC多600、ETH空400：整体相抵绝对200；逐合约绝对和1000，两种都能是实现但只能按批准一种判定；同合约多600空400另测200 |
| `position_ratio_margin_utilization` | contract_pending T04；GAP-004/拟CALC-058 | gross=1000、net=200、initial_margin=100、equity=500，可区分200%、40%、20%。缺分母/非正权益和杠杆>100%保留；不将这三个指标互相顶替 |
| `currency_conversion` | contract_pending T10；GAP-005 | BNB费用 .01，事件价300→3 USDT、期末价350→3.5 USDT；正式选择时点后固定唯一结果。缺汇率不能直接当0或1，内部转账需单账户/组合分别验 |
| `excess_return`、`var_cvar`、`r_squared` | 原型提及但当前M表未定公式与窗口 | 超额收益基准资产/对齐时点；VaR分位方法/置信度/持有期；R²回归目标/收益频率均未明确。登记contract_pending，不声称已实现全部公式 |
| `consecutive_wins_losses`、`max_trade_loss` | 原型有概念，交易样本单位仍受T06/T07约束 | 已确认闭合净交易PnL后可用[1,2,0,-1,-2,3]区分平手打断/忽略的连胜连亏；最小负PnL=-2。平手/费用/同时间排序没定不拍脑袋 |
| `flat_days_longest_run`、`drawdown_recovery_time` | 原型有概念，完整日/修复排序未定 | 持仓0/非0日序列与缺失日分别准备；回撤路径含已恢复、未恢复、并列峰值，需确认Top5按深度还是耗时及谷底到恢复/峰值到恢复。不能拿未知日当空仓 |

## 必须通过真实业务操作验收的跨公式场景

| workflow | 动作与期望 | 对应现有主用例 |
| --- | --- | --- |
| `first_eligible_baseline` | 历史G=500不等于Gclear=500；首个合格快照建立Gbase，未合格不能先建账单。追溯政策见T11/GAP-009 | CALC-041、GAP-009 |
| `lock_then_successor_cycle` | D10000/Gbase500/G1100/rate.2锁单应收120；C2立即Gbase1100；后G1200只给C2收益100 | CALC-042、SET-007、AL-006 |
| `frozen_bill_while_profit_changes` | 锁单后G900或G1200，旧应收仍120；查询刷新不能重复生成业务事实 | AL-007、SET-055 |
| `late_correction` | 原锁单收益600修订580，旧应收120不静默改116；4的差额进入正式核验路径；C2不能再被重复扣差额 | CALC-049、SET-009、GAP-008 |
| `attribution_only_change` | 账户总收益600不变，策略400/150变350/200；账户账单仍120；归因变化不重复出账 | SET-010、CALC-043 |
| `policy_next_cycle` | gate/rate从5%/20%改8%/30%；冻结周期和旧账单不改，后继周期按正式生效规则 | SET-004、046 |
| `receipt_registration_confirmation` | 登记120不计实收；确认90→实收90/待收30；补30→足额；重复凭证/审核不能重复收款 | SET-013～022 |
| `clearance_and_three_bases` | C1锁单G600、C2基准600；G800清算→Gclear800；G900后累计率9%，展示100/10800，C2收益300；真实G/W不清零 | CALC-033～037、SET-030～035、050 |
| `clearance_idempotency_failure` | 失败不推进清算/重置基准；超时查询终态再幂等重试；只允许一次真实资金动作 | SET-038～045、054 |
| `account_isolation_stop_unbind` | A操作不改变B；停止/解绑不抹掉已锁应收，归档不删除历史；同账户多策略只一账单 | SET-005、006、011 |

## 实施与统计要求

- confirmed 算式可以提供独立 Decimal 预期，但必须从原始输入推导，不能把预期当输入注入再读回。
- contract_pending 先登记差异化样本及需要的契约字段，配置正式profile后才启用数值断言；缺契约不算产品 Bug，也不算已通过。
- workflow 必须调用真实锁单、确认、清算等动作并核对前后结果。只返回 `frozen=true`、`idempotent=true` 等测试端自造标识不算覆盖。
- 每个公式族明确列出已有场景、新增场景、可自动执行、规则待定、缺入口的数量，参数化实例不与主用例数混算。
- 不要求每个公式都机械拥有“负值”场景。例如匹配率负数不是合法业务值；需要验证源异常不会被包装成正常结果。也不能让所有CALC套同一套8变体，产生与该公式无关的重复实例。
- 本清单不新增 CALC-051～062 等编号，避免与历史 follow-up 映射冲突；后续自动化按稳定业务 family + 场景名登记，并保留原主用例映射。

## 当前场景文件与真实执行状态

完整项目范围与每项区别性场景见 [全量矩阵](strategy-dashboard-formula-full-matrix.md)，本轮实际执行与明确缺口见 [执行状态](strategy-dashboard-formula-expansion-state.md)。权威文件目录由 `formula_catalog_paths()` 维护，真实执行入口由 `source_case_routes()` 维护；新增的扩展、账本、收款登记、净入金差值以及独立实现 profile 均纳入自动统计。不要继续用下方最初两个文件的271条作为当前总数。

```bash
python3 -m scripts.report_quant_formula_coverage \
  --output reports/quant-formula-coverage.json \
  --markdown reports/quant-formula-scenarios.md
```

生成文件逐条包含中文标题、原始输入、固定预期、主用例映射、自动化入口、实际来源适配状态和单列的待定字段。已有175条主 Case 的元数据验证已与真实业务分开：未开启真实执行时不再当成业务通过。

## 初版场景文件与审计修复记录（历史，2026-09-23早期）

最终读取的两个文件共 **271 个参数化场景、49 个 family**，不改变175条历史主用例数。这里是自动化输入和独立预期的设计/实现统计，不是平台业务通过统计。可用 `python3 -m scripts.report_quant_formula_coverage --output reports/quant-formula-coverage.json` 重新生成每条场景的适配状态。

- `formula_finance.json`：224条、30个family；142个数值结果、74个不可计算结果、8个具有具体输入的规则阻塞结果。216条具有数值/空值断言。
- `formula_contract_pending.json`：47条、19个family；全部有区别性输入和明确待定决策，不再保留`inputs={}`；每条`contract_requirements`说明待签认点及为什么无法唯一计算，预期统一`blocked_contract`。预期没有调用Oracle生成。
- 合计216条数值/空值场景、55条规则待定场景。目前12条已绑定实际输入表和真实查询接口，其余204条可判定场景仍需来源适配。真实原始数据写入、接入、计算、查询、清理链路须以执行证据证明；本文件不宣称271条均在真实平台通过。

### 初版固定场景索引（新增场景以自动生成清单为准）

| family | 当前场景ID | 数量 |
| --- | --- | ---: |
| wallet | FM-WALLET-001～007 | 7 |
| net_deposits | FM-DEPOSIT-001～012 | 12 |
| cumulative_return | FM-RETURN-001～008 | 8 |
| interval_profit | FM-INTERVAL-001～009 | 9 |
| post_clear_return | FM-CLEAR-001～007 | 7 |
| billing_principal | FM-PRINCIPAL-001～005 | 5 |
| billing_profit | FM-PROFIT-001～005 | 5 |
| billing_return | FM-BILLRETURN-001～006 | 6 |
| annualized_return | FM-ANNUAL-001～011 | 11 |
| daily_returns | FM-DAILY-001～008 | 8 |
| annualized_volatility | FM-VOL-001～006 | 6 |
| sharpe | FM-SHARPE-001～007 | 7 |
| calmar | FM-CALMAR-001～006 | 6 |
| drawdown | FM-DRAWDOWN-001～008 | 8 |
| nav | FM-NAV-001～005 | 5 |
| contribution | FM-CONTRIB-001～008 | 8 |
| plan_match | FM-MATCH-001～010 | 10 |
| order_completion | FM-ORDER-001～010 | 10 |
| weighted_fill_price | FM-FILLPRICE-001～011 | 11 |
| positions | FM-POSITION-001～008 | 8 |
| execution_latency | FM-LATENCY-001～005 | 5 |
| execution_latest_times | FM-LATESTEXEC-001～004 | 4 |
| receivable | FM-RECEIVABLE-001～005 | 5 |
| billing_gate | FM-GATE-001～008 | 8 |
| receipts | FM-RECEIPT-001～013 | 13 |
| overdue | FM-OVERDUE-001～005 | 5 |
| bill_summary | FM-BILLSUM-001～007 | 7 |
| account_totals | FM-ACCOUNTS-001～011 | 11 |
| health_weighted_scores | FM-HEALTHSUM-001～004 | 4 |
| position_totals | FM-POSFIELDS-001～005 | 5 |

此前174场景审计发现的本地预期代码问题已按后述新场景落地：成交均价订单隔离见FM-FILLPRICE-006～008；币种门禁见FM-DEPOSIT-009～012、FM-RECEIPT-010～013、FM-BILLSUM-006、FM-INTERVAL-009、FM-POSITION-006/008；交易所/环境账户身份见FM-ACCOUNTS-006；市场仓位身份见FM-POSITION-007；整行缺成员见FM-CONTRIB-007/008；截止点最新账户快照及全部基础字段分别见FM-ACCOUNTS-007～011；同窗口/口径判断见FM-ANNUAL-009～011、FM-SHARPE-006/007、FM-CALMAR-005/006。收款正向时间、健康度待定标题、分润启用语义已在当前数据中修正。下面旧审计段是修复前的历史记录，不是当前仍开放的问题清单。

负钱包、缺E、负Gclear、正负贡献抵消、零延迟、浮盈与margin反向缺失、拒绝收款、空持仓和最近执行时间亦已添加。来源手续费负支出到C正支出的输入归一化仍属于来源适配器验收，不能仅用公式层G-C+F的结果冒充已验完映射。Sharpe主路径已替换为7天8个明确UTC权益边界，少于7天反例使用6天7个边界，避免用365天分子配2个日收益；Calmar同窗案例提供完整起止时间及全部权益变化事件。真实接入是否保持这些时点仍须以实际入口证据验证。

最终审查再补13条区别性输入：有效成交缺数量/价格或数量单位冲突（FM-FILLPRICE-009～011）；匹配null/缺字段/明确false（FM-MATCH-008～010）；最新订单状态null/缺字段/未知（FM-ORDER-008～010）；M04负调整本金（FM-CLEAR-007）；M16负本金/真实零收益（FM-BILLRETURN-005～006）；所选账单缺整行（FM-BILLSUM-007）。FM-VOL-005/006改成空权益历史和缺权益边界，不再直接提交已计算的日收益。

FM-DAILY-008、FM-INTERVAL-009、FM-DEPOSIT-010、FM-POSITION-008保留“缺日或未换算混币不可直接计算”的确定断言，移出规则待定集合。未来缺口填补/汇率转换方式仍待签认，不影响当前禁止错误计算的守卫。FM-CLEAR-001和FM-RECEIVABLE-004只声称静态算式，不声称执行过清算或冻结政策。FM-BILLSUM-002用两张选中账单和一张未选中账单验证集合汇总，不再声称验证跨页接口。

### 当前规则待定具体场景索引

| family | 场景ID与数量 | 区别性输入及待定点 |
| --- | --- | --- |
| win_rate | FCP-WINRATE-001～004；4 | 同一闭合交易拆成两成交、平手、部分平仓、手续费使毛盈转净亏；样本单位和分母 |
| profit_factor | FCP-PROFITFACTOR-001～003；3 | +100/+20/-10区分均值比6与总因子12；全盈利、全亏损的空集合与分母 |
| sortino | FCP-SORTINO-001～003；3 | 真实权益推导正负零日收益，全样本/负样本下行偏差，MAR频率，现金流权益与策略净值对象 |
| fx_fees | FCP-FX-001～003；3 | BNB费用事件价/期末价、缺事件价使用旧价、互转手续费与奖励分类 |
| health | FCP-HEALTH-001～002；2 | 原始指标位于候选端点两侧；既定分数到等级的映射；权重不是待定项 |
| first_baseline | FCP-BASELINE-001～003；3 | 启用到首合格快照间收益、历史清算未迁入、亏损恢复高水位政策 |
| runtime_day_policy | FCP-DAYPOLICY-001～003；3 | 停机使自然10天/有效7天、UTC与UTC+8边界、168小时/完整七日 |
| fixed_principal_nav_cash_flows | FCP-FLOWNAV-001～002；2 | 日内入金有流前后权益、不同资本账户的资本加权/等权篮子 |
| revision_propagation | FCP-REVISION-001～002；2 | 锁单与后继累计修订、已确认款退款；旧冻结事实不得静默改写 |
| exact_evidence_policy | FCP-EVIDENCE-001～003；3 | 121/120秒与未来时点、G/D跨水位、中间入金、USDC到USDT计费资格 |
| top6_tie_breaker | FCP-TOP6-001～002；2 | 第六/七名同分、巨大负贡献按净额/绝对额排序区别 |
| net_exposure | FCP-NETEXPOSURE-001～002；2 | BTC多/ETH空跨合约抵消，同合约跨账户抵消、方向净额/绝对额 |
| position_ratio | FCP-POSITIONRATIO-001～003；3 | gross/net/initial margin分子，挂单冻结，逐仓分母与风险方向 |
| benchmark_excess | FCP-BENCHMARK-001～002；2 | BTC/ETH基准收益差异，缺起点、当日未收盘价格 |
| var_cvar | FCP-VAR-001～002；2 | 原始收益/损益序列、分位插值、边界并列样本、多日复合/时间缩放 |
| r_squared | FCP-RSQUARED-001～002；2 | y=x+常数区分截距，常数基准及配对时间缺口 |
| consecutive_wins_losses | FCP-STREAK-001～002；2 | 平手打断/忽略，同时间两交易次级排序 |
| flat_days | FCP-FLATDAYS-001～002；2 | 日内5分钟持仓与日末空仓区别，空仓日之间缺失观测 |
| drawdown_recovery | FCP-RECOVERY-001～002；2 | 并列谷底首末时间，六段回撤按深度/时长Top5得不同均值及未恢复段 |

19个family的47条输入已经完成场景设计，但仍未批准具体业务规则、未进入真实数值验收；需要的决策逐条保存在JSON中。下述10组真实财务workflow也没有因为这些待定输入存在就算完成。

## 历史：2026-09-23 首次174场景审计快照（已被上述当前状态替代）

本节审读的是 `tests/data/strategy_dashboard/formula_finance.json` 当时的 **174 个场景、40 个 family**，不是执行报告。其中固定期望为 `ok` 的 108 个、`unavailable` 的 47 个、`blocked_contract` 的 19 个。155 个具备数值/不可计算预期的场景仍需真实原始数据入口；19 个规则阻塞场景只是缺口登记。全部都不能据此称为平台公式已通过。

| 实际 family | 已有场景 ID | 当前覆盖边界 |
| --- | --- | --- |
| wallet | FM-WALLET-001～005 | 正负浮盈、零、高精度、缺U；缺负钱包与缺E |
| net_deposits | FM-DEPOSIT-001～008 | 合格/非合格、重复、负净额、空/缺集合、窗口、跨账户；缺币种与完整身份门禁 |
| cumulative_return | FM-RETURN-001～008 | 正负零G、D正零负/缺、独立基准；多账户比率明确blocked |
| interval_profit | FM-INTERVAL-001～008 | 费用/F增量、非交易资金/U隔离、负净额、缺端点、相邻窗口、counter reset；缺来源负支出C转换 |
| post_clear_return | FM-CLEAR-001～005 | 清算点、清算后正负收益、零分母、缺基准；缺负Gclear但分母仍正、负分母 |
| billing_principal | FM-PRINCIPAL-001～004 | 正/零Gclear、负本金、缺基准；缺负Gclear减小正本金 |
| billing_profit | FM-PROFIT-001～005 | 正负零收益、费用/F/U不入计费、缺Gbase；不等于真实新周期建立已覆盖 |
| billing_return | FM-BILLRETURN-001～004 | Gbase/Gclear分离、负收益、零分母、缺基准；缺负分母 |
| annualized_return | FM-ANNUAL-001～008 | 7天门槛、365天、小数日、负值、缺日、本金无效；当前样本全部账户累计分子，需明确scope而非声称全部同口径年化 |
| daily_returns | FM-DAILY-001～008 | 原始权益正负日收益、零、缺点、单点、乱序未闭合、现金流/缺日blocked；缺负前权益、相同UTC时点不同偏移去重 |
| annualized_volatility | FM-VOL-001～006 | ddof=1、三样本、恒定收益、单日/空/缺样本；部分输入为已算returns，不覆盖原始权益接入完整链 |
| sharpe | FM-SHARPE-001～005 | 正负零分子、零波动、未7天；缺明确不同窗口拒绝。现有365日分子+3权益点无时间，不能证明同窗 |
| calmar | FM-CALMAR-001～004 | 最大/当前回撤区别、负收益、无回撤、缺历史；缺同窗/零年化/未7天独立边界 |
| drawdown | FM-DRAWDOWN-001～008 | 时间顺序、恢复/新高、100%回撤、非正起点、缺历史、抽稀、乱序；缺并列峰值与相同资金事实去重 |
| nav | FM-NAV-001～005 | 累计/展示基准分离、清算归1、亏损、缺Gclear、D0但周期分母正；不是M30投资净值 |
| contribution | FM-CONTRIB-001～005 | 正负、全负、全0、重复、某值null；缺正负完全抵消及应有成员未返回的集合不完整 |
| plan_match | FM-MATCH-001～005 | 目标数加权、更新目标、显式不合格、空目标、取消资格待定；输入matched已是判定，不代表原始目标与实际仓位匹配计算 |
| order_completion | FM-ORDER-001～005 | 唯一订单最新状态、全/部分完成、空集合、账户筛选；缺跨来源局部order_id隔离 |
| weighted_fill_price | FM-FILLPRICE-001～005 | 数量权重、重复、零数量/无效成交、跨合约、微小数量；缺最重要的同订单隔离 |
| positions | FM-POSITION-001～005 | 非零多空、重复、最新平仓、跨账户、名义缺失；缺market/position identity隔离 |
| execution_latency | FM-LATENCY-001～004 | 同链、时区跨日微秒、缺同链完成、负时差；缺0秒；“多条completed取最后”需明确最终完成事件语义 |
| receivable | FM-RECEIVABLE-001～005 | 第8位半入边界、乘法先于入账舍入、全部收益计费、零比例；不证明真实冻结费率/有效比例政策已覆盖 |
| billing_gate | FM-GATE-001～008 | 阈值下/等/上、停用、证据false、利润0/负、本金0；缺负本金及required字段缺失；enabled应映射分润政策不是账户运行状态 |
| receipts | FM-RECEIPT-001～009 | 已确认/待审、实际到账时间、重复、跨月时区、缺金额、确认cutoff、空选择、超收待定；缺币种、拒绝凭证、同付款分配多个账单的身份 |
| overdue | FM-OVERDUE-001～005 | strict >、足额、缺due、未批规则；缺到期前样本但同公式已有逻辑；真实due起算仍未覆盖 |
| bill_summary | FM-BILLSUM-001～005 | 唯一账单、同集合收款、指定集合、零/空进度、单单超收；缺不同币种和不同账户同局部bill ID的选择身份 |
| account_totals | FM-ACCOUNTS-001～004 | 多策略去重、零/负权益、缺权益、空集合；目前只聚合equity，未覆盖W/U/G/C/F及状态组；缺交易所/环境身份 |
| health_weighted_scores | FM-HEALTHSUM-001～004 | 固定权重、缺项、零分、未经批准阻断；未验证raw risk→score端点 |
| position_totals | FM-POSFIELDS-001～003 | 正负浮盈、同类margin、margin缺失、混类型；缺浮盈缺失仍保留margin、完整空集合 |
| 11个待定 family | FM-HEALTH-001、FM-SORTINO-001、FM-WINRATE-001、FM-PF-001、FM-FX-001、FM-BASELINE-001、FM-DAYPOLICY-001、FM-FLOWNAV-001、FM-REVISION-001、FM-EVIDENCE-001、FM-TOP6-001 | 全部为inputs={}、expected=blocked_contract；只有缺口占位，无具体原始样本或数值覆盖 |

### 历史发现的本地预期逻辑与输入设计问题（修复映射见上文）

以下通过只读 `evaluate_formula` 最小调用复现，属于测试代码问题，不能输出为平台产品 Bug。审计时没有修改 Oracle 或 JSON。

1. **成交均价跨订单混算。** `order_id=o1`，相同合约的 `o1:1@100` 与 `o2:1@300` 同时返回，当前得到200；M22只允许同订单，正确应只取o1得到100，或缺目标订单范围时拒绝跨订单集合。现有FM-FILLPRICE没有order_id，未覆盖原始订单归属。
2. **同币种限制缺失。** `receivable=100,currency=USDT` 加一笔同账单`currency=USD,amount=100`，当前实收100/待收0；`net_deposits` 把100USDT+1BTC合计101。最新文档禁止未获批跨币直接相加。需要不同币种明确不可汇总/拒绝的场景，不能用一个`fx_fees`空占位代替。
3. **物理账户身份不足。** 两个不同交易所（或不同环境）的`account_id=a,equity=100`当前被合并为账户数1、权益100；正确应是两物理账户、200，或要求适配器先构造并验证完整稳定身份。逻辑载荷须显式保留交易所/环境，不能靠未声明假设。
4. **仓位身份缺市场。** 同账户、同symbol、同side，但分别spot/perp，当前因相同时间冲突而blocked，不能保存为两个仓位。PDF明确账户、市场、合约、方向及仓位身份；加入market/position identity后应count2、gross200。
5. **已指定对象集合缺成员不可见。** 比如指定应有a/b策略，但只返回a=100，当前贡献仍为100%。仅用已有FM-CONTRIB-005“成员行存在但profit=null”不能代替整行没取得；需要选择全集/完整性字段进入逻辑载荷，缺成员时完整贡献不可算。
6. **同窗与截至时点未被验证。** 现有Sharpe/Calmar部分样本用365天分子搭配2～3天日边界且无时间字段；只能证明算术，不能证明同窗。当前账户汇总也忽略cutoff，可把cutoff之后快照当当前。真实适配器应验证各输入范围/观测时点，必要字段未提供时不能称完整链路覆盖。
7. **状态语义标题需修正。** FM-HEALTH-001写“端点权重缺正式签认”，但权重已确定，只有端点/等级待定；FM-GATE-004必须解释为分润政策未启用，不能把账户停止运行等同停用分润。FM-RECEIPT-002第二笔到账时间在登记审核之后，是不合理正向输入，应调整为两笔到账均先于各自登记审核但两种最大时间不同。

### 历史建议补充的区别性场景（当前落地情况见上文）

优先补上述七类中的确定性分支，再补这些不依赖新增业务规则的输入：

- 负钱包：E=100、U=150→W=-50，验证未被clamp到0；权益缺失而U已知→钱包null。
- 负清算基准：D=1000、Gclear=-200、G=-100→本金800、展示收益率.125；与本金≤0的不可计算区分。
- 正负贡献完全抵消：[100,-100]→绝对分母200、占比+.5/-.5，不能因净额0变成不可算。
- 零延迟：同链触发与完成均T→0秒，而不是null；实际完成缺失仍null。
- 同类型margin已知、浮盈null：unrealized=null但used_margin保留，反向验证FM-POSFIELDS-002已覆盖的字段隔离。
- 来源手续费以负支出表示：来源费用-10经明确符号映射为C=10后，ΔG100、ΔF-3→87；不能直接把-10代入M02得到107。该项应覆盖输入适配器，不修改固定M02公式。
- 选定账单已确认、待审、拒绝各一笔：只累加confirmed；存在拒绝事实不降低已确认实收，重复查询不生成负收款。
- 账户金额全字段：一账户绑定两策略，E/W/U/G/C/F去重后只各算一次；若G缺失只阻断G的完整总额，不抹掉W/E完整值。当前FM-ACCOUNTS只覆盖E。

### 历史 workflow 与待定合同评估

上节列出的10组workflow没有被本JSON实现；其中真实锁单冻结、后继Gbase、到账审核、清算更新Gclear、幂等/失败、跨月确认和更正，需要业务操作接口及隔离资源。不能用FM-PROFIT-004、FM-CLEAR-001这类静态代数样本替代动作前后断言。

当时11个空输入待定family不能称场景设计完成；本次已删除空占位并以独立文件47条有区别性输入替代，映射见当前索引。未获批准时只返回规则待定，不猜数值、不计数值通过。来源身份、币种、同窗和数据完整性是已明确约束，不属于这些待定公式，不能被一并搁置。
