# Factor 4.0 全量测试结果

测试环境：`test-factor-frontend.questvector.ai/mcp/factor-data`，使用现有测试配置和测试 MySQL。
开始：2026-09-08 15:40:23 +08:00；结束约 16:21:52；耗时 2488.923 秒，约 41 分 28 秒。
原始报告：`reports/factor4-full-live-20260908.xml`。退出码 1，不覆盖为通过。

## 实际执行范围

```bash
python3 -u -m pytest tests/cases/factor4 --live --env test \
  --include-factor4-internal-calculation --include-factor4-technical --include-factor4-deferred \
  -v --tb=short -rs -o junit_family=xunit1 \
  --junitxml=reports/factor4-full-live-20260908.xml
```

全部 167 个测试函数、668 个参数化实例均进入本轮，未使用 fail-fast；三个专项开关全部开启。
没有默认 deselected 或 `DEFERRED_SCOPE`；跳过是运行时的证据、契约、条件或适用性判断。
没有启动远端计算、发布、反馈提交或数据库写入。报告中没有额度/请求限流/依赖不可用错误。
111 条 pytest warnings 不计业务失败；业务结果以以下 JUnit 统计为准。

| 范围 | 实例 | 通过 | 失败 | 跳过 |
| --- | ---: | ---: | ---: | ---: |
| 核心结果级 | 478 | 373 | 5 | 100 |
| 原始计算专项 | 36 | 19 | 2 | 15 |
| 技术专项 | 15 | 10 | 4 | 1 |
| 历史暂缓专项，本轮显式执行 | 139 | 65 | 65 | 9 |
| **合计** | **668** | **467** | **76** | **125** |

0 errors。76 是失败实例数，不是产品 Bug 数；静态一项可含多个 finding，同一结果差异也可命中多个入口。

## 结果差异与证据缺口

### 发布摘要路由数量与实际有效路由数量不一致

- **复现**：核心两个入口均失败，但归为同一问题。
- batch 6、`all/default/WIDE_RANGE` 摘要 `route_count=86`，同发布身份的 active eligible route 实际 `83`。
- 入口：`test_environment_closure_business.py::test_each_label_metrics_routes_and_summary_share_full_publication_identity[WIDE_RANGE]`，以及 `test_route_environment_references.py::test_factor4_route_environment_references`。
- 后者其他三个 finding 是时区/日期语义阻塞，不是新增失败。
- 预期：摘要计数与实际有效结果一致。其余五环境该闭环通过。

### 有效路由排名不连续

- **复现，独立根因未定**：83 条有效 route 的排名不连续，初次与重复读取均报 `RANK_NOT_CONTIGUOUS`；没有 publication 切换或重复读取不稳定。
- 本轮正式报告验证连续性失败。此前同日定向证据的缺位为 74、75、84，排名范围 1 至 86；不将前次缺位明细冒充本轮 XML 新输出。
- 入口：`test_final_results.py::test_final_result_ranking_is_partitioned_and_repeatable`。
- 预期：同分区 active eligible 排名为 `1..N`。与摘要计数问题可能相关，不先计成两个独立根因。

### 公式绑定缺证据

- **正式发布结果追溯缺口，不等同计算错误**：本轮核验 83 个 active 引用，其中 32 个没有 completed formula evidence。
- 结束后的测试 DB 只读复核证实这 32 个在 `factor_ic_run_formula_evidence` 完全没有行：

  ```text
  49,62,65,80,103,106,151,158,160,163,172,194,207,212,216,217,
  256,339,388,417,433,439,455,461,526,527,545,549,577,583,886,887
  ```

- 均为 `sub_factor` ID。核心入口 `test_formula_catalog_business.py::test_all_active_formula_projections_and_approved_inputs` 失败；内部语义组合入口重复命中此缺口，不另计问题。
- R0 另报 5724 个 metric、477 个因子缺 run/hash/version 强链接，涉及面不同，不能与 32 相加。

### 环境快照缺失日期与当时可用环境记录不一致

- **候选，选择契约待确认**：`2024-09-02` 被冻结快照列为缺失，但 daily `1341` 是当时可见记录；失败码 `ENV_FROZEN_MISSING_DATE_HAS_VISIBLE_ENVIRONMENT`。
- 入口：`test_environment_closure_business.py::test_frozen_missing_dates_match_full_range_daily_history_at_batch_as_of`。
- 同检查还有 daily `2068` 的选择规则阻塞；不作为第二个 Bug，也没有证实因子数值受到影响。

### 公式已更新但 normalized_formula 元数据仍保留旧表达式

- **确认公式输出互相矛盾**：`sub_factor:161104、161106、161108`，本轮静态检查命中后以真实 MCP 批量详情定向复核。
- 同一 executable 详情的 `calc_logic` 是 `mean(close, window) - close.shift(window // 2 + 1)`，而 `metadata.normalized_formula` 仍是 `-(close - mean(close, (60 + 0)).shift(((60 + 0)) // 2 + 1))`。
- 前者移动价格，后者移动均线，不是单纯格式差异。最新 DB detail 不再含该 normalized 元数据，MCP 仍从其他来源返回旧值。
- 复现工具：`factor_get_details_batch`，参数 `{"factor_refs":["sub_factor:161104","sub_factor:161106","sub_factor:161108"],"detail_level":"executable"}`。
- 不能据此宣称当前 DPO 实际数值仍算错，也不重命名为旧 DPO 计算 Bug。
- 该静态 Case 另有 9 个 `DB_DETAIL_FORMULA_MISMATCH`：106250、148744、161532、879、880、881、883、886、887。6 个是 vwap/rolling_vwap 名称差异、2 个是 log_returns/returns、1 个为 vol_percentile_bucket 与滚动波动率表达式差异。缺别名或数学语义契约，单列内部计算待裁定，不把 AST 不同直接认定为 9 个计算 Bug。

### Slice 结束时间边界错误

- **本轮复现，保留历史暂不处理归类**，不混入核心 478 项结论。
- 入口：`test_run_selection_and_slice_scopes.py::test_exact_slice_end_equality_returns_contained_database_rows`。
- 同一 Run/完整 scope 的代表样本 `sub_factor:276`，`symbol=""`，切片 ID `209756322`，起点 `2026-09-06T00:00:00Z`，终点 `2026-09-07T00:00:00Z`。
- 结束时点小于终点 1 微秒或恰好相等：`METRIC_SCOPE_NOT_FOUND`；终点加 1 微秒：返回该切片，且没有下一页。
- 这是等于边界的明确对照，不是未配置 scope。沿用现有用例的包含结束点预期，历史暂缓状态不因本次执行自动取消。

## 其他失败的裁决

| 固定名称/分类 | 失败实例 | 事实及裁决 |
| --- | ---: | --- |
| 参数校验错误的文本响应被误判为 MCP 返回格式错误 | 58 | 全部经过本地 JSON-only 解析失败路径。抽样 schema extra、feedback integer、factor_search float 均 HTTP 200、isError=true、纯文本参数拒绝、没有业务数据；不能当作 58 个产品 Bug，也未逐项改判为通过 |
| Accept 协商用例错误要求仅声明 SSE 的 POST 请求成功 | 1 | SSE-only 返回 406/-32600，Case 却要求成功；属于请求/预期问题 |
| 协议版本协商用例错误限制服务端只能返回配置版本 | 1 | 请求 2099-01-01 实际返回受支持的 2025-11-25，直接请求后者也成功；Case 错误要求只能返回本地 2025-06-18 |
| 子因子分页重放用例将动态游标变化误判为业务数据变化 | 3 | factor:84 同一 cursor 间隔1.2秒重读，children 均 sub_factor:3841、其他 data 完全一致，唯一变化 children_next_cursor；不能比较不透明游标字符串来证明数据漂移 |
| 批量指标查询对重复因子引用的返回规则未明确 | 1 | 代表 CS 样本 sub_factor:1460705 返回两个成功位置，metric_id=1135965、Run 和 data 相同；Case 强制拒绝/去重，公开工具尚未明确规则。不是原 TS 节点逐项改判 |
| 计算结果审计记录缺少 request_id | 4 | daily 2113、batch 4、metric 11448、route 86 条缺 request_id。技术审计缺口，不能据此判计算数值错误 |

MCP 工具错误允许 `isError=true` 与文本内容，不要求错误正文也是 JSON；本地解析器在验证业务拒绝前就失败。
依据：[MCP Tools](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)。
POST Accept 需同时声明 JSON/SSE，版本协商可返回其他受支持版本。
依据：[Transports](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)、[Lifecycle](https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle)。
上述 63 个本地判定问题与 1 个批量契约项，加 Slice 边界 1 个，合计原暂缓专项 65 个失败。
本轮只执行和裁决，没有修这些本地用例，也没有将 JUnit 中的失败改为通过。

## 跳过项

125 个 skip 互斥分类：90 个数据/证据前置、28 个契约/时区/数学语义未定、2 个 stateless 会话不适用、4 个研究状态 warning 条件未触发、1 个本地有界敏感字段扫描未覆盖全集。

- 六环境 TS/CS 组合准入的 18 个实例：14 个缺对应 both/ts_only/cs_only 样本；3 个 WIDE_RANGE 缺冻结权重；1 个 CHOPPY_UP ts_only 有指标但无 route 可验权重。neither 六环境全部通过。
- 准入阈值/null/fallback/clip 仍有 8 个实例缺自然样本，包含 87 个 scope×分支键；87 不是用例数量，不能与 skip 相加。
- signed 排名有 3 个实例缺明确方向，coverage 4 个缺数据，Top/Bottom 重叠补位规则 14 个待确认。
- 另有公式 run/hash 绑定、父因子发布及聚合、superseded 历史、反馈归属样本、同分排序和发布原子契约缺口，详见每个 XML skip 原因。
- 本次没有旧 QA batch 7 相关 skip 或推荐快照误报，不能沿用上一轮 QA 缺口说明。旧问题未命中也不等于 QA 样本本身已修复，可能不再进入当前 active 样本。

## 已通过的重点范围

- 38 项目录扩展全部通过，包括状态、分类、中文检索、详情与父子分页。new-sub_factor 单链40页/2000唯一ID，DB3421，记录 `BOUNDED_READ_ACCEPTED_NOT_FULL_EXPORT`，不是全库导出通过。
- Backend 三方对账 7 项、研究搜索 47 项、validity/slice 基础15项全部通过。
- 当前 v1 评分独立对账、TS/CS 全部已冻结拒绝规则、成本结果、OOS 时间、直接因子版本检查通过。
- 币种级 TS 各排名指标、普通 CS 排名数值、汇总单查/显式Run/批量、默认与历史 Run 选择通过；signed 有前述方向/契约缺口，不宣称所有排名分支全通过。
- 固定窗口公式族专项通过；DPO/IV-RV 的实际批次公式绑定仍因证据不足跳过，不能以详情投影通过代替实际计算验收。

报告完整 Token 形态扫描未命中；没有改写历史运行报告。此次补充仅为结果记录及固定标题登记，测试代码保持执行时版本。
