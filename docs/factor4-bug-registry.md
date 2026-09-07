# Factor 4.0 Bug Registry

本文件是 Factor 4.0 测试问题的唯一命名登记表。登记时间：2026-09-04（Asia/Shanghai）。

2026-09-06 迁移说明：以下状态、数量和历史数据仍是登记日的记录，不代表本次迁移回归结果。
本次只更新已删除脚本的复现入口，保留固定中文标题及历史证据；当前执行结果见 `docs/factor4-script-migration.md`。
新版命令读取现有测试配置并动态发现数据库样本，不依赖历史报告中的固定 Run。

2026-09-07 全区间历史补齐新增一项 `CANDIDATE`：“环境快照缺失日期与当时可用环境记录不一致”。下方历史数量表不据此当作全量最新验收统计；新项详情单独保留真实证据和未确认的选择契约。

## 命名规则

1. **固定中文标题**是对外唯一 Bug 名称。问题再次出现、回归测试或验收复测时必须逐字使用同一个标题。
2. 英文索引（例如 `F4-ENV-BACKEND-EXACT-FILTER`）只用于内部检索，不替代固定中文标题。
3. Case ID（例如 `ENV-108`）只表示测试场景，不替代固定中文标题。一个根因覆盖多个 Case 时只登记一个 Bug。
4. 历史中文名称、旧 Case 名和脚本名作为别名保留；修复后只更新 `状态`、`最后验证` 和证据，不重命名固定中文标题。
5. `CONFIRMED` 才计入当前产品 Bug；`CANDIDATE`、`BLOCKED`、`DEFERRED` 和 `CLOSED` 不计入确认数量。
6. 本轮的 pytest 框架失败和测试账号前置失败不自动归因于 Factor 4.0 产品；它们在文末单独登记。

## 当前数量

| 口径 | 数量 | 说明 |
|---|---:|---|
| 正式 100-case 独立产品 Bug | 5 | 5 个 P1 根因；`DB-605` 是汇总 Case，不另计 |
| 历史论文/运行时专项确认问题 | 2 | 不计入正式 100-case 统计，但仍是待处理问题 |
| 当前确认问题合计 | 7 | 下表 `CONFIRMED` 的产品问题 |
| 待契约确认候选 | 4 | 不计 Bug 数量，避免把测试预期或未定契约误报为产品缺陷 |

## 固定中文标题总表

以下标题是后续测试报告、验收单和回归记录的唯一对外名称。英文索引和 Case 仅作定位。

| 固定中文标题 | 英文索引 | 关联 Case |
|---|---|---|
| Backend 环境日期精确过滤遗漏已有环境记录 | `F4-ENV-BACKEND-EXACT-FILTER` | `ENV-108`, `DB-605` |
| Backend 指标周期时间戳时区转换错误（整体偏移 8 小时） | `F4-METRIC-PERIOD-TZ` | `MET-310`, `DB-605` |
| DPO 公式错误地位移均线而非价格序列 | `F4-DPO-FORMULA` | `CALC-510` |
| 固定周期因子公式未应用声明窗口 | `F4-FIXED-HORIZON-FORMULA` | `CALC-510` |
| 发布摘要路由数量与实际有效路由数量不一致 | `F4-PUBLISHED-ROUTE-COUNT` | `DB-613` |
| 论文候选与注册因子语义映射错误 | `F4-KB-MAPPING-SEMANTIC-MISMATCH` | 论文/KB 专项 |
| IV/RV 因子定义与实际执行公式及输入字段不一致 | `F4-IV-RV-DEFINITION-RUNTIME-MISMATCH` | IV/RV 专项 |
| 公式已更新但 normalized_formula 元数据仍保留旧表达式 | `F4-NORMALIZED-FORMULA-STALE` | `CALC-510-A`，当前元数据专项 |
| 环境快照缺失日期与当时可用环境记录不一致 | `F4-FROZEN-MISSING-DATE-VISIBLE` | 环境全区间历史核验 |

### 环境快照缺失日期与当时可用环境记录不一致

- **英文索引**：`F4-FROZEN-MISSING-DATE-VISIBLE`
- **当前状态**：`CANDIDATE`，2026-09-07。按可见 ready 环境日期应进入快照的规则，用例失败；额外筛选契约尚待确认，不提前计为确认产品 Bug。
- **已确认事实**：批次 `6`、`7` 的冻结区间为 `2024-09-02` 至 `2026-09-01`，`missing_dates` 明确包含 `2024-09-02`。同一测试库日历记录 `1341` 对应该日 `fact / CHOPPY_UP / ready / revision=1`；`available_at=2026-08-31 15:43:44.121786`、`created_at=2026-08-31 15:43:50.999883`，均早于批次 6 的冻结时点。批次 7 复用了相同快照，不能算第二次独立计算复现。
- **时间口径**：批次列 `as_of_time=2026-09-02 01:17:17.390102`，冻结 JSON 为 `2026-09-01T17:17:17.390102Z`。这两种表示尚未作契约确认；本条日历记录早于两者超过一天，不能以一次常见的 8 小时时区转换消除此事实差异。
- **预期 / 实际**：若缺失定义为 as_of 时没有可用 ready 日历，则该日不应列为缺失；实际已声明缺失。没有证据证明这次差异已经改变因子数值或最终排名。
- **复现命令**：

  ```bash
  python3 -m pytest tests/cases/factor4/test_environment_closure_business.py::test_frozen_missing_dates_match_full_range_daily_history_at_batch_as_of --live --env test -v --tb=short
  ```

- **验证步骤**：在同一只读事务读取批次的冻结日期区间、as_of、members、missing_dates；独立查询该完整日期区间内同 label_kind 的所有 daily 修订，不按成员 ID、is_current 或 available_at 预过滤；按 as_of 选可见的最高修订，核对 ready 日期是否被列为 missing；另查创建时间排除事后回填。
- **证据**：`reports/factor4-closure-range-history-final-live-20260907.xml`。失败码为 `ENV_FROZEN_MISSING_DATE_HAS_VISIBLE_ENVIRONMENT`。
- **根因边界**：不能断定是开始日期边界或时区代码错误；当前公开日期字段未说明额外选择条件。`2026-08-30` 的 latest 日历为 not_ready，该日只记录选择规则不明，不当作同类失败。

### 公式已更新但 normalized_formula 元数据仍保留旧表达式

- **英文索引**：`F4-NORMALIZED-FORMULA-STALE`
- **当前状态**：`CANDIDATE`，2026-09-06 本地判定器修正后尚未进行独立线上复核，不计入本轮确认数量。
- **历史事实**：2026-09-05 保存的报告中，`sub_factor:161104/161106/161108` 的 `calc_logic` 和精确公式 evidence 已使用正确 DPO，但 `metadata.normalized_formula` 仍保留位移均线的表达式。
- **根因边界**：这是元数据一致性问题，不能沿用「DPO 公式错误地位移均线而非价格序列」来断言实际运行错误。归一化前后仅周期参数不同且没有实际输入 cadence/单位转换证据时，只记录 `BLOCKED_DOC`；不得直接判为错误或通过。
- **测试判定修正**：家族回归只读取可执行 detail 与精确 Run evidence。当前详情静态审计独立核验归一化元数据，同层输入字段分别对账，禁止把逻辑输入与 raw dependency closure 合并后比较。
- **历史证据**：`reports/factor4-formula-regression/20260905T034346Z/adjudicated-results.json`、`reports/factor4-deep/20260905T110208Z-dpo-formula-recheck/report.json`。

## 确认的产品 Bug

### Backend 环境日期精确过滤遗漏已有环境记录

- **英文索引**：`F4-ENV-BACKEND-EXACT-FILTER`
- **历史别名 / Case**：`ENV-108`；“Backend 精确 environment_date 过滤漏行”；`DB-605`（汇总）
- **状态 / 严重度**：`CONFIRMED` / `P1`
- **归属**：Backend 环境查询；不是 MCP 传输或协议问题。
- **影响**：调用方按文档传入精确 `environment_date` 时，Backend 返回空集合或漏行；同条件 MCP 和数据库仍能命中当前记录，造成三方结果不一致。
- **已确认事实**：`fact` 日期 `2026-09-02` 应命中 `id=2182`；`forecast` 日期 `2026-09-03` 应命中 `id=2183`。Backend 带精确日期返回 `[]`，不带日期可以返回记录；带不带 `as_of` 均复现。
- **复现命令**：

  ```bash
  python3 -m pytest tests/cases/factor4/test_backend_three_way_business.py::test_backend_daily_exact_date_and_pit_match_mcp_database --live --env test -v
  ```

- **核心请求**：

  ```text
  GET /api/v1/market-environments/daily?label_kind=fact&environment_date=2026-09-02&include_revisions=false&limit=10
  ```

- **预期 / 实际**：预期 Backend 返回包含 `id=2182` 的 fact；实际返回 `[]`。forecast 用 `2026-09-03` 同样预期 `id=2183`，实际漏行。
- **最新证据**：`reports/factor4-resume/20260904T071013Z-calc508-env108-met310/adjudicated-summary.json`。
- **根因边界**：已确认 exact-filter 行为错误；具体部署代码中的 SQL/时区根因尚未直接验证。

### Backend 指标周期时间戳时区转换错误（整体偏移 8 小时）

- **英文索引**：`F4-METRIC-PERIOD-TZ`
- **历史别名 / Case**：`MET-310`；“Backend 指标 period 时间比 DB/MCP 早 8 小时”；`DB-605`（汇总）
- **状态 / 严重度**：`CONFIRMED` / `P1`
- **归属**：Backend 时间序列化；不是 MCP 协议问题。
- **影响**：同一指标的身份、数值和 run 可以一致，但 Backend 的 `period_start/period_end` 表示了错误的瞬时时点，调用方按时间窗口回放或对账会发生边界偏移。
- **已确认事实**：因子 `sub_factor:1325857` 的 cross-sectional 指标 `982821` 和 time-series 指标 `982822`，数据库 `metrics_json` 使用 UTC `2026-07-25T00:00:00Z`、`2026-07-26T00:00:00Z`；MCP 正确显示为 `2026-07-25T08:00:00+08:00`、`2026-07-26T08:00:00+08:00`；Backend 显示为 `2026-07-25T00:00:00+08:00`、`2026-07-26T00:00:00+08:00`，早 8 小时。
- **复现命令**：

  ```bash
  python3 -m pytest tests/cases/factor4/test_backend_three_way_business.py::test_backend_summary_identity_values_and_period_instants_match_mcp_database --live --env test -v
  ```

- **复现步骤**：对同一 `factor_ref`、metric ID 和 run，分别读取 Backend、MCP 和数据库 `metrics_json` 的 `period_start/period_end`，先统一为 UTC 再比较；Backend 两个端点各出现 `-08:00` 偏移。
- **最新证据**：`reports/factor4-resume/20260904T071013Z-calc508-env108-met310/adjudicated-summary.json`。
- **根因边界**：已确认序列化结果不一致；“naive datetime 被本地时区化”只是合理推测，未直接验证部署代码。

### DPO 公式错误地位移均线而非价格序列

- **英文索引**：`F4-DPO-FORMULA`
- **历史别名 / Case**：`CALC-510-DPO`；`CALC-510` 中的 DPO 公式问题；DPO 论文因子窗口问题
- **状态 / 严重度**：`CONFIRMED` / `P1`
- **归属**：持久化因子定义/计算公式；不是 MCP 传输问题。MCP、DB 和 active route 反而一致地暴露了错误公式。
- **影响因子**：`sub_factor:161104`（detail `169779`）、`sub_factor:161106`（detail `169781`）、`sub_factor:161108`（detail `169783`）。
- **已确认事实**：当前持久化公式为 `-(close - mean(close, 60).shift(31))`，等价于移动均线先位移；独立 DPO oracle 为 `SMA(close, 60) - close.shift(31)`。合成序列在 warmup 后 `150/150` 个点不一致，index 150 实际约 `-834.6896`、oracle 约 `39.1515`。
- **复现命令**：

  ```bash
  python3 -m pytest tests/cases/factor4/test_formula_catalog_business.py -k dpo --live --env test -v
  ```

- **复现步骤**：读取每个因子的 detail、immutable formula evidence 和最新 completed run；用独立序列 `close[i] = 100 + i^2/17 + 0.13*(i mod 7)` 重算两种公式并比较 warmup 后输出。
- **最新证据**：`reports/factor4-deep/20260904T071236Z-dpo-formula-recheck/report.json`。
- **边界**：本 Bug 不包含历史 VWAP 观察项；VWAP 另按 `DEFERRED` 记录。

### 固定周期因子公式未应用声明窗口

- **英文索引**：`F4-FIXED-HORIZON-FORMULA`
- **历史别名 / Case**：`CALC-510-FIXED-HORIZON`；`CALC-510` 中的固定周期窗口公式问题
- **状态 / 严重度**：`CONFIRMED` / `P1`
- **归属**：因子公式生成/持久化；不是 MCP 传输问题。
- **影响因子**：`sub_factor:181`（detail `325`，声明 48h）、`sub_factor:183`（detail `327`，声明 72h）、`sub_factor:274`（detail `418`，声明 48h）、`sub_factor:276`（detail `420`，声明 72h）。
- **已确认事实**：
  - `181/183` 当前均为 `funding_rate.diff(12).diff(12)`，原始依赖跨度 24 bars；按家族语义分别应进入 `diff(24).diff(24)` 和 `diff(36).diff(36)`。
  - `274/276` 当前均为 `long_short_ratio.pct_change(24)`，原始依赖跨度 24 bars；按声明窗口分别应为 `pct_change(48)` 和 `pct_change(72)`。
  - 控制因子 `sub_factor:180`（24h）通过，说明问题集中在非 24h 变体的窗口参数未进入公式语义。
- **复现命令**：

  ```bash
  python3 -m pytest tests/cases/factor4/test_formula_catalog_business.py::test_fixed_horizon_declared_and_completed_formula_candidates --live --env test -v
  ```

- **复现步骤**：读取 detail 声明窗口、immutable formula evidence 和最新 completed run 的原始依赖 offsets；比较声明 bars 与最大依赖跨度，并按同一公式家族生成独立 oracle。
- **最新证据**：`reports/factor4-resume/20260904T150933+0800-fixed-horizon-adjudication/summary.md`。
- **边界**：这是一个公式族问题，四个因子共用同一个固定中文标题；不要拆成四个重复 Bug。

### 发布摘要路由数量与实际有效路由数量不一致

- **英文索引**：`F4-PUBLISHED-ROUTE-COUNT`
- **历史别名 / Case**：`DB-613`；“published environment_status.route_count 与 active route 实际数量不一致”
- **状态 / 严重度**：`CONFIRMED` / `P1`
- **归属**：数据库发布摘要聚合/写入；没有 MCP/HTTP 调用，不是 MCP 问题。
- **已确认事实**：同一只读事务内连续读取 published success batch `6`、同一 publication/version 和 `WIDE_RANGE`：摘要 `environment_status.route_count=0`；按相同身份条件精确统计 `active + eligible` route 为 `86`；第二次读取仍为 `0/86`，快照稳定且 route 身份错配为 `0`。
- **复现命令**：

  ```bash
  python3 -m pytest tests/cases/factor4/test_final_results.py::test_each_environment_summary_matches_final_routes --live --env test -v
  ```

- **复现步骤**：在 `START TRANSACTION READ ONLY` 中读取 batch 的 `environment_status.WIDE_RANGE.route_count`；再用同一 `batch/publication_uid/publish_version/market_scope/label_code` 查询 `market_environment_factor_route` 的 active eligible 数量；重复读取一次并回滚。
- **预期 / 实际**：预期摘要数量等于精确 route 数量；实际 `0 != 86`。
- **最新证据**：2026-09-07 新增闭环用例真实只读复核，`reports/factor4-closure-final-live-20260907.xml`；同名问题仍复现 `WIDE_RANGE route_count=0`、实际 active eligible route 为 `86`。本次入口为 `test_environment_closure_business.py::test_each_label_metrics_routes_and_summary_share_full_publication_identity[WIDE_RANGE]`，其余五种 label 的该项对账通过；这不是全量历史 Bug 重新验收。历史证据继续保留在 `reports/factor4-resume/20260904T151150+0800-db613-targeted-closure/adjudicated-summary.json`。
- **边界**：不要把 `DB-605` 单独登记；它只是 `F4-ENV-BACKEND-EXACT-FILTER` 与 `F4-METRIC-PERIOD-TZ` 的汇总失败。

### 论文候选与注册因子语义映射错误

- **英文索引**：`F4-KB-MAPPING-SEMANTIC-MISMATCH`
- **历史别名 / 专项**：论文/KB 映射语义错误；KB 五样本映射错误
- **状态 / 严重度**：`CONFIRMED` / `P1`（历史专项，不计入正式 100-case）
- **归属**：论文候选到因子结果的映射/调度数据链路；不是 MCP 传输问题。MCP 查询均为 HTTP 200，问题在返回对象语义。
- **已确认事实**：以下 5 对对象均返回 `mapping_status=mapped`、`result_validity=valid`，且 detail 查询 HTTP 200，但候选字段、频率或经济含义与实际子因子不一致：

  | extraction | result sub-factor | 候选语义 | 实际因子语义/字段 |
  |---:|---:|---|---|
  | `64014` | `1597753` | XRP/BTC ETF 周度流入流出背离 | `aggressive_flow_price_divergence_zscore_24h`；1h `close/taker_buy_volume/volume` |
  | `64009` | `1599721` | Polymarket 事件概率与临近时间 | `event_prob_amplitude_pressure_24h`；1h `long_short_ratio/high/low` |
  | `63998` | `1584138` | 多资产 TVP-VAR 最小关联度组合权重 | `connectedness_premium_corr_168h`；单资产 `close/premium_index` |
  | `64011` | `1592833` | LSTM 日频 7-30 天价格预测 | `lstm_proxy_volatility_sentiment_regime_24h`；1h `close/long_short_ratio` |
  | `64000` | `1585108` | PoW/ESG 多资产能源溢价篮子 | `crypto_energy_esg_vol_volume_zscore`；1h `close/volume` |

- **复现命令**：本轮直调报告已保存；若需要再次核对，可按报告中的 5 个 `kb_factor_candidate_search` 请求逐个发送，并用同一 `mapped_factor_id` 调 `factor_get_detail`。上一轮完整验收脚本的结果也保留在 `reports/factor4-deep/20260904T111452+0800-kb-ts-bug-acceptance-rerun/acceptance-summary.json`。
- **复现步骤**：对每个 extraction ID 做精确 KB 查询；读取返回的 `dependent_data_fields/data_frequency/holding_period`；再读取 mapped sub-factor detail 的 `formula_summary/factor_bar_interval/fields`；只要身份虽 mapped 但这些核心语义不一致，即复现。
- **最新证据**：`reports/factor4-deep/20260904T072026Z-kb-mapping-five-recheck-v2/mapping-summary.json`（5/5 MCP 查询成功，DB 只读事务回滚）。
- **边界**：`task_status=failed` 或 quota 阻断本身不另计 Bug；本记录只针对已存在的错误映射。

### IV/RV 因子定义与实际执行公式及输入字段不一致

- **英文索引**：`F4-IV-RV-DEFINITION-RUNTIME-MISMATCH`
- **历史别名 / 专项**：IV/RV 定义与执行公式不一致
- **状态 / 严重度**：`CONFIRMED` / `P1`（历史专项，不计入正式 100-case）
- **归属**：因子定义、可执行公式和数据源能力之间的契约/运行时链路；不是 MCP 传输问题。
- **影响因子**：`sub_factor:161628`（detail `170303`，30d）、`sub_factor:161629`（detail `170304`，7d）、`sub_factor:161630`（detail `170305`，90d）。
- **已确认事实**：定义声明 `ATM_IV - realized_vol`，但 immutable formula evidence、执行公式和当前输入字段仍是 `close.pct_change().rolling(window).std()` 一类 realized-vol 计算；`ATM_IV` 与 `realized_vol` 被列为缺失源字段，旧 active route/metric 仍存在。定义、运行时公式和可用字段不能同时满足论文/因子定义。
- **复现命令**：

  ```bash
  python3 -m pytest tests/cases/factor4/test_formula_catalog_business.py -k iv_rv --live --env test -v
  ```

- **复现步骤**：分别读取 detail summary、definition、executable formula、raw schema 和 metrics；核对定义字段集合与执行字段集合，并检查 active route 是否仍指向该定义。
- **最新证据**：`reports/factor4-deep/20260904T071317Z-iv-rv-definition-recheck/report.json`。
- **边界**：这是 3 个 tenor 变体的同一根因，不拆成 3 个 Bug；缺少真实 IV/RV 源数据是运行阻断的一部分，但不抹去定义与执行证据已经冲突的事实。

## 待契约确认的候选（不计 Bug）

这些记录保留固定名称，后续若产品契约确认为缺陷，直接把状态改为 `CONFIRMED`，不得重新命名。

### Top-up 因子公式与父主题公式不一致

- **英文索引**：`F4-TOPUP-FORMULA-FALLBACK-SEMANTICS`
- **状态**：`CANDIDATE`
- **现象**：active top-up 路由审计发现 145/192 个 top-up 公式与同父主题、同窗口的正式因子不同；部分使用 `close.pct_change().rolling(window).mean()` 等通用补足公式。
- **为什么暂不计 Bug**：当前产品契约没有确认 top-up 是否允许通用 fallback 公式替代父主题公式。仅凭公式不同不能证明实现错误。
- **证据**：`reports/factor4-deep/20260904T071552Z-kb-topup-route-audit/summary.md`。

### 嵌套组合因子是否允许的接口契约未确定

- **英文索引**：`QT-COMBO-NESTED-COMPOSITE-CONTRACT`
- **状态**：`CANDIDATE`
- **现象**：测试先登记一个组合因子，再把它作为新组合成员提交，Backend 返回 HTTP 422：`combination factors cannot be used in another factor combination`。
- **为什么暂不计 Bug**：现有测试名称预期允许嵌套，但服务端错误信息明确禁止嵌套；需要产品契约先决定哪一个是正确行为。
- **证据 / 用例**：`tests/cases/factor_combo/test_combo_scenarios.py::test_registered_composite_sub_factor_can_be_used_in_a_new_form`。

### 工作单 direction 字段正负值契约矛盾

- **英文索引**：`QT-WORK-ORDER-DIRECTION-CONTRACT`
- **状态**：`CANDIDATE`（更像测试框架契约矛盾）
- **现象**：真实母因子工作单返回成员 `direction=-1`；Service 的 `_required_response_int` 默认先要求 `direction >= 1`，而后续成员校验又要求 `direction in {-1, 1}`。
- **为什么暂不计 Bug**：需要先确认接口文档是否允许负方向。若允许，修复测试框架校验；若不允许，才登记 Backend 数据 Bug。
- **证据 / 用例**：`tests/cases/factor_combo/test_combo_scenarios.py::test_real_parent_factor_flow_reaches_a_classified_terminal_outcome`；`service/factor_combo_service.py`。

### Feedback action 字段未纳入测试框架映射

- **英文索引**：`QT-FEEDBACK-ACTION-MAPPING`
- **状态**：`CANDIDATE`（测试框架映射遗漏）
- **现象**：Feedback API 和数据库业务断言均通过，但响应额外返回 `action=continue_exploration`，当前 persistence allowlist 未映射该字段，测试以 `FAIL_CONTRACT` 结束。
- **为什么暂不计 Bug**：API 已明确返回且业务状态正确；当前证据更支持测试框架字段映射遗漏，不应直接归因后端。
- **证据 / 用例**：`tests/cases/factor_combo/test_feedback_api.py::test_feedback_rejects_combo_and_resets_form_for_next_round`；`service/factor_combo_persistence.py`。

## 测试环境/前置阻断（不计产品 Bug）

| 固定中文标题 | 英文索引 | 本轮表现 | 需要的处理 |
|---|---|---|---|
| restricted 测试账号权限配置不符合负向场景 | `QT-RESTRICTED-ACCOUNT-FIXTURE` | 2 个权限负向用例 setup error；restricted 账号实际拥有 `use_factor_agent`。 | 提供确实不含受测权限的测试账号，或明确调整权限负向契约。 |
| non-owner 测试账号被封禁导致所有权场景阻断 | `QT-NON-OWNER-ACCOUNT-FIXTURE` | 7 个所有权隔离用例 setup error；non-owner 登录 HTTP 403，返回“账号已封禁”。 | 提供已启用且具备业务权限、但不是资源所有者的账号。 |
| factor_reader_db 推荐依赖不可用 | `EXT-READER-DB` | 推荐正向/PIT 分支返回 `factor_reader_db/DEPENDENCY_UNAVAILABLE`。 | 恢复 reader DB 后重跑受阻分支。 |
| 生命周期写入测试门禁未开启 | `EXT-R1-HMAC-SCHEDULER` | 生命周期写入、故障注入和历史切换用例未开启写入门禁。 | 提供授权 JWT/HMAC、专用 fixture 和清理窗口。 |

## 已关闭或按用户要求暂缓的历史记录

以下名称保留用于历史追踪，但当前不计入开放 Bug。除非用户明确要求重新纳入，否则不要在“当前未修复 Bug”列表中重复报告。

| 固定中文标题 | 英文索引 | 历史名称/现象 | 当前状态 | 备注 |
|---|---|---|---|---|
| 币种级 time-series 排名完全不可用 | `F4-RANK-TS-SYMBOL-SCOPE` | “币种级 time-series 排名完全不可用” | `CLOSED` | 后续排名/validity 复验通过；旧失败来自错误的历史行 oracle。 |
| Parent 指标自动选择返回旧周期 | `F4-PARENT-METRIC-STALE-PERIOD` | “Parent 指标自动选择返回旧周期” | `CLOSED` | 最新 parent/ranking 快照专项未发现新的 FAIL。 |
| VWAP 实际为累计 VWAP 而非滚动窗口 | `F4-VWAP-CUMULATIVE-WINDOW` | “VWAP 实际为累计 VWAP，非滚动窗口” | `DEFERRED` | 按当前测试指令跳过；不得并入 DPO 或 fixed-horizon Bug。 |
| 孤儿记录 | `F4-ORPHAN-RECORD` | 孤儿记录 | `DEFERRED` | 用户明确暂不处理。 |
| Slice 结束时间边界错误 | `F4-SLICE-END-TIME-BOUNDARY` | 结束时间边界错误 | `DEFERRED` | 用户明确暂不处理。 |
| 引用不存在的文档 | `F4-UNAVAILABLE-DOCUMENT-REFERENCE` | 引用不存在的文档 | `DEFERRED` | 用户明确暂不处理。 |
| 体验、规范与兼容性观察 | `F4-EXPERIENCE-COMPATIBILITY` | 体验、文案、规范、兼容性观察 | `EXCLUDED` | 按测试口径去除，不作为功能 Bug。 |

## 本轮执行索引

### pytest 全量

```bash
python scripts/run_tests.py --env test --report /tmp/questtest-offline-20260904.xml
python scripts/run_tests.py --env test --live --report /tmp/questtest-live-20260904.xml
```

- 离线：`318 collected / 191 passed / 127 skipped / exit 0`。
- 测试环境 live：`318 collected / 306 passed / 3 failed / 9 errors / 0 skipped / exit 1`。
- 3 个失败对应上文 `QT-COMBO-NESTED-COMPOSITE-CONTRACT`、`QT-WORK-ORDER-DIRECTION-CONTRACT`、`QT-FEEDBACK-ACTION-MAPPING`；9 个 errors 是账号前置阻断，不计产品 Bug。

### Factor 4.0 专项

本轮 priority 专项均在测试环境执行；数据库检查使用只读事务并回滚，未执行生产写入：

100-case 汇总文件：`reports/factor4-resume/20260904T152617+0800-final-coverage/coverage.md`（`PASS 40 / FAIL 5 / BLOCKED 54 / EXCLUDED 1`）。

| 专项 | 结果/证据目录 |
|---|---|
| Protocol gap | `reports/factor4-resume/20260904T070313Z-protocol-gaps` |
| Tool matrix / PIT | `reports/factor4-resume/20260904T150434+0800-tool-matrix-pit` |
| Catalog boundary / KB remaining | `reports/factor4-resume/20260904T150559+0800-catalog-boundaries`、`reports/factor4-deep/20260904T151423+0800-catalog-kb-remaining` |
| Route integrity | `reports/factor4-resume/20260904T150646+0800-route-integrity-closure` |
| Temporal oracle | `reports/factor4-resume/20260904T150714+0800-temporal-oracle-closure` |
| Status/permission | `reports/factor4-resume/20260904T150814+0800-status-permission-closure` |
| Ranking/parent | `reports/factor4-resume/20260904T150850+0800-ranking-parent-snapshot-closure` |
| Fixed horizon | `reports/factor4-resume/20260904T150933+0800-fixed-horizon-adjudication` |
| DB-613 | `reports/factor4-resume/20260904T151150+0800-db613-targeted-closure` |
| DPO / IV-RV / KB | `reports/factor4-deep/20260904T071236Z-dpo-formula-recheck`、`reports/factor4-deep/20260904T071317Z-iv-rv-definition-recheck`、`reports/factor4-deep/20260904T072026Z-kb-mapping-five-recheck-v2` |

后续报告必须使用本文件的固定中文标题；英文索引只用于机器检索。状态变化示例：`DPO 公式错误地位移均线而非价格序列：CONFIRMED -> RESOLVED`；中文标题和英文索引均保持不变。
