# Catalog / KB / Universe / Protocol 迁移分支记录

本记录只说明代码替代程度，不能把 `PARTIAL`、单元测试或缺样本计为 live 通过。
本组 13 个来源现在均已逐分支核销为 `ASSERTED` 并删除。异常输入、兼容、并发仍按用户既有范围暂缓执行，但已实现真实请求/业务断言，以 `factor4_deferred` 标记，需额外指定 `--include-factor4-deferred` 才执行；live/test/host 门禁不变。下面历史迁移表保留用于审计，最新收口以本节和 registry 为准。

## 最新完整替代（收口状态）

- `test_protocol_deferred_business.py` 完整实现：20 个只读工具的真实 DB baseline、实时 extra/enum/limit 最小最大及越界/整数类型；JSON-RPC 精确错误码、未知版本、JSON/SSE、Session、独立连接并发、严格双表示、1000 行环境全分页；目录/children cursor 篡改和跨 kind/filter/limit/parent/tool；混合存在/不存在 ref；语义非法日期/ID/类型；真实移除 Authorization、假/畸形 Bearer、Origin、认证回显。反馈写工具只读取 schema，不调用。
- `test_catalog_extension_business.py` 补齐十个 kind×status、四分类、中文名查询、非匹配成功空末页；原有六条件交集、三个详情级别、50 条批量、顺序/重复/类型隔离、children 全页仍保留。
- `test_research_search_business.py` 补齐独立 **factor_search** 的 TS 币种/TS 汇总/CS 汇总、候选全集/数值阈值/PIT、explicit valid/invalid/unknown 统计、TS-only/CS-only overall 搜索。不是用 factor_rank 替代。
- `test_feedback_status_business.py` 用本次 request_id 精确关联 active API key/owner/scope，owned 状态字段 DB 对账，不存在/其他主体隔离；13 类输入异常均有真实断言。
- `test_backend_three_way_business.py` 补齐环境日期/PIT、summary 身份/最终数值/period 瞬间三方、推荐 publication/forecast/有序因子/score 三方、全部 active published 的费用空值/payload/route TS-CS evidence、OpenAPI **静态** HMAC 头声明。
- `test_schema_business.py` 承接完整字段/raw schema DB 对账、选择字段依赖、重复读取和错误 selector/version；其他发布/指标/公式分支使用 registry 中明确的跨文件 node。
- 普通 `test_protocol_read_business.py` 只证明完整业务 structured 数据和请求关联；严格 text JSON 与 structuredContent 一致性由 deferred 独立 Case 验证，**普通读取通过不等于严格双表示通过**。

### 已删除的 13 个来源

`catalog_deep_readonly.py`、`catalog_deep_expansion.py`、`catalog_deep_recheck.py`、`catalog_kb_remaining_probe.py`、`filter_error_kb_deep.py`、`catalog_search_functional_deep.py`、`catalog_boundaries_resume_probe.py`、`protocol_boundary_probe.py`、`protocol_gap_closure.py`、`feedback_status_input_matrix.py`、`status_permission_closure.py`、`calc508_env_met_reconcile.py`、`kb_topup_route_audit.py`。

全部恢复源保存在父任务完整迁移备份；本组另有 `/tmp/questtest-catalog-migration-backup.B6y58X`，已跟踪文件也可由 Git 恢复。没有删除配置、测试凭据、静态数据或报告。Case 不依赖 tmp 或历史结果文件。

### 明确不计覆盖/通过的分支

1. 旧 stats 完成指标 distinct factor_ref 与 library 实体数口径混用的错误 oracle 已移除；分别验证口径。numeric 阈值只传 search，不给 stats 传 schema 未支持参数。
2. 全库前后 count/更新时间不能证明某次 MCP 没有写库，只作漂移诊断；不复制虚假只读 guard。
3. 用户限定最终结果，原始 bar/收益/持仓/换手/费用重算不在范围；寻找 hash-only/原始列仅诊断，不能套固定成本 0.001 到其他冻结配置。
4. status_permission 的 ENV110/MET305 是硬编码历史计数/判词，不是实时可执行场景。ordinary browse PAT/只读 DB 凭据原本没有提供且只是固定 BLOCKED；不拿 full_access/允许的测试写账号冒充，权限负向仍未验收。
5. 内部 HMAC runtime 需要内部 POST，超出只读迁移权限；静态声明通过不等于运行权限通过。
6. Topup 同父/window 的公式字符串差异、KB文本语义差异没有明确等价契约，仍是风险候选；不搬固定 FAIL/candidate 文案。
7. quota gate 只观察自然 EXPORT_BUDGET_EXCEEDED，没有阈值契约；服务端畸形 SSE 原为不可适用，客户端不能强制远端产生。本地解析反例仅 unit。
8. `filter_error_kb_correct.py`、`field_runtime_mcp.py`、`field_runtime_db.py` 仅报告修订/风险证据捕获，保留 NON_CASE_TOOL，不计业务用例。

### 最新定向验证补充

- 新十状态/四分类/中文查询：`reports/factor4-catalog-status-expanded.xml`，16 passed。
- 反馈/基础 Backend：`reports/factor4-feedback-backend-migration-current.xml`，8 passed / 14 skipped（13 deferred、1 当前主体无 owned feedback）。
- 本组最近 8 个单测文件：71 passed；这是离线反例，不是真实业务通过。
- 扩大费用对账到全部 active published 后，24 条 `qa_six_labels_20260905` 人工 fixture success 缺费用字段/route 费用证据。其 payload 明确写明 Synthetic API fixture、未执行真实计算，因此保留失败但归类 **fixture evidence gap**，不能宣称真实计算错误；真实 batch 6 的 4134 条 success 三项费用均非空。
- Backend 推荐未投影独立 returned_count，已改为仅明确返回时对账；完整有序 items 仍必须精确一致。最终结果以父任务全量报告为准，不能拿以下旧定向报告当最新总数。

## 早期定向报告（仅历史证据）

- `reports/factor4-catalog-protocol-migration-current.xml`：早期 56 个实例，55 passed / 1 skipped（claimed 无自然任务）。后续代码修改由最终全量报告覆盖。
- 旧 PARTIAL 表已移除，避免已完成的代码迁移被误读为仍未实现；逐来源具体节点和不计覆盖分支永久保存在 `service/factor4_case_registry.py`。
- 所有新 Case 只读、不导入 tmp、不依赖旧报告、不输出 Token、密码或响应正文；执行门禁跳过不算 live 通过。
