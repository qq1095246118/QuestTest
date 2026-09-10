# Factor 4.0 Bug Registry

本文件是 Factor 4.0 测试问题的唯一命名登记表。登记时间：2026-09-04（Asia/Shanghai）。

2026-09-06 迁移说明：以下状态、数量和历史数据仍是登记日的记录，不代表本次迁移回归结果。
本次只更新已删除脚本的复现入口，保留固定中文标题及历史证据；当前执行结果见 `docs/factor4-script-migration.md`。
新版命令读取现有测试配置并动态发现数据库样本，不依赖历史报告中的固定 Run。

2026-09-07 全区间历史补齐新增一项 `CANDIDATE`：“环境快照缺失日期与当时可用环境记录不一致”。下方历史数量表不据此当作全量最新验收统计；新项详情单独保留真实证据和未确认的选择契约。

2026-09-07 额度恢复后结果级复跑已结束：369 passed、6 failed、0 errors、263 skipped（139 历史暂缓、124 其他阻塞），没有额度错误。
本次失败已区分候选、正式追溯证据缺口、QA 证据缺口和判定器问题，不直接计为六个产品 Bug。
最新结果及每项复现见 [本轮复跑记录](factor4-result-live-20260907-rerun.md)；下方“当前数量”仍为历史登记口径，不用于这次统计。

## 2026-09-07 结果级复跑固定名称

| 固定中文标题 | 本轮裁决 |
| --- | --- |
| 公式绑定缺证据 | 正式 batch 6 的 32 个已发布子因子没有运行公式证据；更广的 metric-to-run 强链接也缺失，不等同于公式算错 |
| 环境快照缺失日期与当时可用环境记录不一致 | 仍为 CANDIDATE；daily 1341 的同一日期差异复现，尚无已确认数值影响 |
| `new` 状态子因子目录分页验收失败 | 2026-09-08 契约澄清：预算终止属于预期保护，不计产品 Bug；原失败归属测试判定器，修正记录见下方 |
| 成本结果证据不完整 | QA batch 7 的人工样本缺字段，不是已证实成本计算错误 |
| 因子定义版本证据不完整 | QA batch 7 缺独立 definition 版本，不是已证实正式版本混用 |
| OOS 时间证据不完整 | QA batch 7 缺 folds，不是已证实未来数据泄漏 |
| 推荐结果回查误报指标快照变化 | 测试判定器比较原列与 JSON 投影导致差异，不是实际快照漂移 |

2026-09-07 当时样本回归通过的历史标题：Backend 环境日期精确过滤遗漏已有环境记录；
Backend 指标周期时间戳时区转换错误（整体偏移 8 小时）；发布摘要路由数量与实际有效路由数量不一致。
历史证据及未纳入本轮的专项状态继续保留，不按本次通过推断所有历史问题均已修复。

2026-09-08 本地目录修正及去重后的 31 项定向回归：29 passed、2 failed，目录相关 24 项全部通过。
“发布摘要路由数量与实际有效路由数量不一致”再次出现，本次摘要 86、实际有效 route 83。
另一失败固定名称为“有效路由排名不连续”，其与摘要数量差异是否同一根因尚未确定，先作为候选保留，不重复增加独立根因计数。
详情见 [目录修正与用例去重](factor4-catalog-case-cleanup-20260908.md)，不是全量历史 Bug 复测。

2026-09-08 后续全量已执行全部 668 实例：467 passed、76 failed、125 skipped、0 errors，三个专项均开启。
核心结果级 478 中 373 passed、5 failed、100 skipped。76 为实例数，不是独立 Bug 数；
详细归并、公式元数据定向复现及历史边界观察见 [本次全量结果](factor4-full-live-20260908.md)。
本次没有修改用例来改变结果，旧历史总数不自动替换为本轮独立根因数。

### `new` 状态子因子目录分页验收失败

- **最新裁决**：2026-09-08，`NOT_A_PRODUCT_BUG`；固定中文标题保留作为原误报的追踪名称。
- **规则依据**：用户补充防批量导出用途及每分钟 2000 条限制；[MCP 使用说明第 3.2、5.6 节](https://jjp1ynw9z1yy.jp.larksuite.com/wiki/AdOhwoJLMiII4HkCSfJjUB2RpXg)明确单链到达预算后终止 cursor 并返回 warning。文档默认值可由部署配置覆盖。
- **事实边界**：9 月 7 日同链返回 2000 条后明确 `CATALOG_CURSOR_BUDGET_REACHED`，不是静默丢失其余 1420 个已有记录。该现象没有验证分钟限流或一分钟后的恢复机制，不混同日预算、分钟限流和单链累计量。
- **测试修正**：目录受限终态只核验已返回数据；自然末页才要求完整集合。无说明的提前终止、重复及错误数据仍失败，报告保留受限范围。
- **进度及证据**：测试判定器已修正，41 个离线实例通过，14 个目录 live 实例通过；其中本项返回 2000/3421 个因子，按受限范围验收，其余 13 个自然完结并对账完整集合。详见 [现有 Case 修正记录](factor4-existing-case-corrections.md) 及 `reports/factor4-catalog-bounded-live-20260908.xml`。原全量 JUnit 的 6 failed 保持历史原貌，不事后改写。

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
| 有效路由排名不连续 | `F4-ELIGIBLE-ROUTE-RANK-GAP` | `RESULT-504`，最终排名结果级检查 |
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
- **当前状态**：`CONFIRMED`，2026-09-08 全量静态检查及真实 MCP 批量详情定向复核，确认公式输出元数据互相矛盾；不证明当前计算数值错误。
- **本轮事实**：`sub_factor:161104/161106/161108` 的 executable 详情 `calc_logic=mean(close, window) - close.shift(window // 2 + 1)`，`metadata.normalized_formula=-(close - mean(close, (60 + 0)).shift(((60 + 0)) // 2 + 1))`。最新 DB detail 不含 normalized 字段，MCP 仍返回旧元数据。复现调用 `factor_get_details_batch`，`factor_refs` 为上述三个，`detail_level=executable`；详见 [全量结果](factor4-full-live-20260908.md)。
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
  python3 -m pytest tests/cases/factor4/test_environment_closure_business.py::test_each_label_metrics_routes_and_summary_share_full_publication_identity --live --env test -v
  ```

- **复现步骤**：在 `START TRANSACTION READ ONLY` 中读取 batch 的 `environment_status.WIDE_RANGE.route_count`；再用同一 `batch/publication_uid/publish_version/market_scope/label_code` 查询 `market_environment_factor_route` 的 active eligible 数量；重复读取一次并回滚。
- **预期 / 实际**：预期摘要数量等于精确 route 数量；实际 `0 != 86`。
- **最新证据**：2026-09-07 新增闭环用例真实只读复核，`reports/factor4-closure-final-live-20260907.xml`；同名问题仍复现 `WIDE_RANGE route_count=0`、实际 active eligible route 为 `86`。本次入口为 `test_environment_closure_business.py::test_each_label_metrics_routes_and_summary_share_full_publication_identity[WIDE_RANGE]`，其余五种 label 的该项对账通过；这不是全量历史 Bug 重新验收。历史证据继续保留在 `reports/factor4-resume/20260904T151150+0800-db613-targeted-closure/adjudicated-summary.json`。
- **2026-09-08 定向复核**：`reports/factor4-catalog-dedup-live-20260908.xml`，同一中文标题再次失败：`WIDE_RANGE` 摘要 `86`，实际 active eligible `83`。9 月 7 日后续样本曾通过的记录保持原貌，不据此覆盖本次差异。
- **边界**：不要把 `DB-605` 单独登记；它只是 `F4-ENV-BACKEND-EXACT-FILTER` 与 `F4-METRIC-PERIOD-TZ` 的汇总失败。

### 有效路由排名不连续

- **英文索引**：`F4-ELIGIBLE-ROUTE-RANK-GAP`
- **状态**：`CANDIDATE`，2026-09-08。有效结果排名不连续已证实；与上述摘要数量差异是否同一根因尚未确定，不先计作第二个独立产品根因。
- **实际结果**：测试库 batch `6`、`all/default`、`WIDE_RANGE`、环境日期 `2026-09-01`，active eligible route 共 `83` 条，rank 范围 `1..86`，缺少 `74、75、84`，没有重复 rank。正式用例的初次与重复读取均报 `RANK_NOT_CONTIGUOUS`，没有 publication 切换或重复读取漂移。
- **预期**：按现有结果级验收规则，同分区的 active eligible 排名应从 1 连续至实际结果数；不据此推断评分计算错误。
- **复现命令**：

  ```bash
  python3 -m pytest tests/cases/factor4/test_final_results.py::test_final_result_ranking_is_partitioned_and_repeatable --live --env test -v --tb=short
  ```

- **验证步骤**：使用测试配置读取当前 active published 分区；按完整 publication/batch/profile/label/环境日期身份筛选 active eligible route；按 rank 排序，与 `1..N` 比较；再次读取同 publication 检查是否稳定。当前样本预期为 `1..83`，实际仍含 `85、86` 且中间缺位。
- **证据**：`reports/factor4-catalog-dedup-live-20260908.xml`。另经只读 Repository 的 `read_published_route_snapshot("all", "default")` 核对当前 83 条及上述缺位，仅输出数字/分区摘要，不读取原始计算过程、不写库。
- **处理边界**：保留用例；不因本次清理而删除失败项，不把两次读取的两个失败码计成两个 Bug。

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

### 2026-09-08 全量执行的固定测试问题名称

| 固定中文标题 | 本轮失败实例 | 裁决 |
| --- | ---: | --- |
| 参数校验错误的文本响应被误判为 MCP 返回格式错误 | 58 | 本地 JSON-only 解析路径问题，代表请求已确认返回明确拒绝；未逐个改判为通过 |
| Accept 协商用例错误要求仅声明 SSE 的 POST 请求成功 | 1 | 本地请求和预期不符合 POST Accept 要求 |
| 协议版本协商用例错误限制服务端只能返回配置版本 | 1 | 服务端实际协商到另一受支持版本，非接受虚构版本 |
| 子因子分页重放用例将动态游标变化误判为业务数据变化 | 3 | 本地比较了动态 children_next_cursor，业务数据一致 |
| 批量指标查询对重复因子引用的返回规则未明确 | 1 | 公开去重/保留输入位置契约待确认，不计产品 Bug |
| 计算结果审计记录缺少 request_id | 4 | 技术审计观察，不据此认定计算结果错误 |

本轮证据和代表复核见 [全量结果](factor4-full-live-20260908.md)。此表不是修复记录；本轮没有修改这些用例。
“Slice 结束时间边界错误”本次显式全开执行后仍复现，保留下面历史暂不处理归类，不混入核心结果级 Bug 数量。

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
