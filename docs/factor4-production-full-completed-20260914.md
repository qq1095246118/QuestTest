# Factor 4.0 生产全量回归完成记录 — 2026-09-14

全部 **171 个测试函数、691 个唯一参数化实例、30 个文件**现已取得本轮终态，未执行完成节点为 **0**。本次为上午全量、用户暂停后的晚上续跑以及定向复核按 nodeid 合并的最新状态，未使用 9 月 11 日结果填补。

| 状态 | 最新数量 |
| --- | ---: |
| PASSED | 403 |
| FAILED | 84 |
| ERROR | 9 |
| SKIPPED | 195 |
| 合计 | 691 |

完成执行不等于全部业务断言通过。84 FAILED 不是 84 个产品 Bug；195 SKIPPED 没有计入通过。当前确认仍未关闭的业务问题 **4 个，其中新增 1 个**。原 TS 范围超时和环境指标大小限制的精确请求验收通过。

## 执行来源与数量校验

- 上午全量原进程被用户暂停，退出码 130，未生成完整 JUnit。原日志实际有 597 个唯一终态：325 PASS / 73 FAIL / 13 ERROR / 186 SKIP。旧解析器漏了参数含空格的 Accept 协商节点，原“596 已完成、95 剩余”已校正为“597 已完成、94 剩余”。
- 晚上 19:35 启动 95 节点续跑：94 个未完成节点，加上述漏计 PASS 的一次复核。结果 71 PASS / 15 FAIL / 9 SKIP，708.078 秒。
- 上午过期 Backend JWT 造成的 6 ERROR 已用配置账号密码登录后复验：5 PASS / 1 FAIL；运行时清空静态 JWT，不修改配置文件。TS summary 原最小请求仍 HTTP 500。
- 晚上失败取证 47 节点：3 PASS / 42 FAIL / 2 ERROR，906.107 秒；活跃因子公式详情、两个 children 游标重放节点恢复 PASS。
- 另有 8 节点错误/生命周期/tags复核，4 FAIL / 4 ERROR；2 个环境跳过代表仍 SKIP。这些均覆盖已存在节点，不增加691。
- 共 755 次有终态的正式执行记录，64 次为重复执行；按节点取最新结果后仍为691。诊断性 MCP/DB 请求不计入正式用例数。
- 最新来源分布：上午原日志533、续跑JUnit95、Backend复验6、错误复核8、环境跳过代表2、失败取证47，共691。原日志的部分失败/跳过细节在中断时未保留，逐节点报告明确标记 TERMINAL_STATUS_ONLY，没有伪造 JUnit 或借用旧栈。

[完整逐节点结果](../reports/factor4-production-full-completed-20260914.json)；
[逐节点裁决与分类](../reports/factor4-production-full-completed-adjudication-20260914.json)；
[校正后的暂停进度](../reports/factor4-production-full-20260914-progress-corrected.json)。

原始日志及正式 JUnit：
[上午日志](../reports/factor4-production-full-20260914-1100.log)、
[Backend](../reports/factor4-production-backend-auth-rerun-20260914.xml)、
[续跑95](../reports/factor4-production-resume-20260914-1935.xml)、
[失败复核47](../reports/factor4-production-resume-failure-review-20260914.xml)、
[错误复核8](../reports/factor4-production-resume-error-triage-20260914.xml)、
[环境代表2](../reports/factor4-production-resume-environment-skip-triage-20260914.xml)。

## 当前确认的业务问题

### Backend TS 指标最小查询超时

固定标题保持原名，本轮实际症状已由无响应超时变为 HTTP 500。
使用 Backend 有效登录鉴权，单次发送：

```http
GET https://factor-backend.questvector.ai/api/v1/factor-ic/summary-metrics?is_sub_factor_id=true&ic_scope=time_series&limit=1
```

预期正常单条或业务空结果；19:38 的最小复核无重试、60秒客户端上限，46.134秒 HTTP500：
`{"error":"查询失败","success":false}`。同一正式TS用例在刷新登录后也失败，CS对照通过，不能归因于过期JWT或泛化成全部Backend失效。
[本轮最小证据](../reports/factor4-production-resume-backend-minimum-20260914.json)。

### DPO 公式错误地位移均线而非价格序列

生产 MCP 完成初始化后调用 `factor_get_details_batch`，参数：

```json
{"factor_refs":["sub_factor:161106","sub_factor:161628","sub_factor:161629","sub_factor:161630"],"detail_level":"executable"}
```

161106 预期现有负DPO约定 `mean(close,60) - close.shift(31)`；实际仍
`-(close - mean(close, (60 + 0)).shift(((60 + 0)) // 2 + 1))`。
9月13日 detail更新时间变化没有修正此公开公式。仅确认当前161106定义，不扩大到未逐项读取的其他DPO或全部发布数值。

### IV/RV 因子定义与实际执行公式及输入字段不一致

复用上一项批量详情调用，检查161628/161629/161630的 formula_summary、calc_logic、params.fields。
三者声明 `ATM_IV - realized_vol`，但执行式均为
`close.pct_change().rolling(window).std()`，输入只有 `["close"]`。
这是同一问题的三个期限，未独立重算历史数值或排名。

两项公式的最新 request_id 均为 `f288f80b-4509-4fed-9ce7-9681d52a95ab`。
[完整请求和必要返回原字段](../reports/factor4-production-resume-known-mcp-20260914.json)。

### 环境标签查询遗漏当前已发布的有效路由

本轮新增固定中文标题。生产 MCP 初始化后发送：

```json
{
  "jsonrpc": "2.0",
  "id": "questtest-20260914-tags-14580",
  "method": "tools/call",
  "params": {
    "name": "factor_get_environment_tags",
    "arguments": {
      "factor_ref": "sub_factor:1435649",
      "market_scope": "all",
      "route_profile_key": "default"
    }
  }
}
```

预期返回该因子的已发布 WIDE_RANGE 路由。实际 HTTP200、isError=false、returned_count=0、items=[]，返回的publication仍是当前batch11。

19:42:14—19:42:17 DB前后身份投影相同：batch11为 running / per_factor_incremental / published / active，route14580为同因子WIDE_RANGE，active=1、eligible=1、rank5、score78.856103，publication_uid=df8596a9-463e-4d87-bf97-17d8af27b58e。
关联metric9918同batch/ref/label，cross_sectional / success / is_valid=1，error_code为空。公开detail和按名称realized_volatility_1d_w24的search也返回相同ref。
这证明正常输出漏项，未观察到请求期间发布切换；不是不存在因子或无效metric。
request_id=`717565ea-ea74-4491-967c-9618e869ebb4`。
[正式失败及最小复现、DB前后核查](../reports/factor4-production-resume-error-triage-20260914-review.json)。

## 已通过的原请求与边界

- **TS 指标范围查询超时，使用端链路无法开始**：原1h/all/time_series/limit1，历史9月11日as_of和当前as_of都成功各返回1条，15.666/17.303秒。不扩大为所有分区完整枚举通过。
- **环境指标最小分页查询仍超过响应大小限制**：原factor149、原batch3603f724-9a14-4c6a-a670-aef51dc19c12、WIDE_RANGE、TS、limit1成功返回原metric2381（0.526秒）；DB确认同一成功有效记录，payload38796字节。可以确认原精确指标请求通过。tags原因子当前为空结果，不作为原非空tags响应的独立验收。
- **Backend 推荐接口查询有效发布结果返回 HTTP 500**：本轮刷新登录后的正式对账通过，不表示所有非空推荐排序分支都已覆盖。
- 当前active因子公式输出对账、children_parent/children_replay正式复核通过。后者既有动态游标比较实现未改，不凭本次PASS声称本地时间敏感风险被消除。

## 未通过状态归因

| 分类 | FAILED | ERROR |
| --- | ---: | ---: |
| 已确认业务读取 Bug | 2 | 0 |
| 内部公式专项失败，仅保留暂停前终态 | 2 | 0 |
| 本地发布批次选择器排除 running 增量发布 | 0 | 3 |
| 反馈能力或表结构前置缺口 | 15 | 4 |
| 已知公式定义问题相关专项失败 | 6 | 0 |
| 本地断言误判 running 增量发布 | 3 | 0 |
| 审计字段技术观察 | 4 | 0 |
| 本地错误响应 JSON-only 判定 | 43 | 0 |
| 协议兼容专项判定 | 2 | 0 |
| 本地 DB 取样查询超时 | 2 | 2 |
| 有效性 null/unknown 表示契约待明确 | 1 | 0 |
| 历史 Slice 边界问题，用户指定暂缓 | 1 | 0 |
| 切片范围限制，原请求证据未保存 | 2 | 0 |
| 重复输入处理契约待明确 | 1 | 0 |
| 合计 | 84 | 9 |

两项内部公式专项只有暂停前FAIL终态，未再次执行耗时约二十分钟的全库AST扫描，不能从耗时推断失败原因；本轮确认公式问题依据独立当前请求。
反馈4ERROR中1个共享前置代表已重验errno1146，另外3个保留上午ERROR，没有声称逐项重新取栈。
4个DB取样超时实例为2ERROR和2FAIL，发生于summary_sample，errno3024，未进入目标搜索业务断言。不是所有DB查询都失败。

当前batch11的running增量发布在已吸收的[规则依据](factor4-deduplicated-result-coverage.md)中允许；本地选择器/断言仅放行success和特定partial_fail。因此3个ERROR、3个生命周期FAIL及部分环境跳过属于测试实现规则不完整，不能报告成线上没有发布结果或增量发布非法。

195SKIP包含上述本地前置阻断，以及自然样本/完整母子汇总/不可变公式版本证据不足等。上午未保留全部skip原因，只对两个共享环境前置代表补取了确切原因，不把所有SKIP一概归为缺数据。
未完成断言的重点仍是当前running增量发布下的环境评分/route/公式闭环、部分母子及不完整validity样本。

续跑TS切片两项QUERY_TOO_BROAD的原sample/request未保存。当前新自然样本的短范围正常，同一因子731天范围明确拒绝“maximum of 366 days”。保留两个正式FAIL，列范围适配观察，不新增Bug或声称精确复现了原请求。
[范围守卫证据](../reports/factor4-production-resume-slice-limit-diag-20260914.json)。

Slice结束时间边界仍按用户要求DEFERRED；VWAP、孤儿、引用不存在文档等历史暂缓项不重新打开。协议/体验/规范观察不加入业务Bug清单。

## 全部文件的最新结果

| 文件 | 总数 | PASSED | FAILED | ERROR | SKIPPED |
| --- | ---: | ---: | ---: | ---: | ---: |
| [test_auxiliary_business.py](../tests/cases/factor4/test_auxiliary_business.py) | 27 | 19 | 0 | 0 | 8 |
| [test_backend_three_way_business.py](../tests/cases/factor4/test_backend_three_way_business.py) | 7 | 6 | 1 | 0 | 0 |
| [test_calculation_logic.py](../tests/cases/factor4/test_calculation_logic.py) | 3 | 0 | 1 | 2 | 0 |
| [test_catalog_extension_business.py](../tests/cases/factor4/test_catalog_extension_business.py) | 38 | 38 | 0 | 0 | 0 |
| [test_consumer_journey_business.py](../tests/cases/factor4/test_consumer_journey_business.py) | 6 | 4 | 0 | 0 | 2 |
| [test_cross_read_business.py](../tests/cases/factor4/test_cross_read_business.py) | 17 | 10 | 0 | 0 | 7 |
| [test_economics_closure_business.py](../tests/cases/factor4/test_economics_closure_business.py) | 5 | 2 | 0 | 0 | 3 |
| [test_environment_closure_business.py](../tests/cases/factor4/test_environment_closure_business.py) | 32 | 0 | 0 | 0 | 32 |
| [test_feedback_status_business.py](../tests/cases/factor4/test_feedback_status_business.py) | 17 | 0 | 13 | 4 | 0 |
| [test_final_results.py](../tests/cases/factor4/test_final_results.py) | 5 | 0 | 0 | 0 | 5 |
| [test_formula_catalog_business.py](../tests/cases/factor4/test_formula_catalog_business.py) | 55 | 24 | 7 | 0 | 24 |
| [test_formula_closure_business.py](../tests/cases/factor4/test_formula_closure_business.py) | 12 | 0 | 0 | 0 | 12 |
| [test_formula_route_audit_migrations.py](../tests/cases/factor4/test_formula_route_audit_migrations.py) | 4 | 0 | 0 | 0 | 4 |
| [test_lifecycle_closure_business.py](../tests/cases/factor4/test_lifecycle_closure_business.py) | 6 | 0 | 3 | 0 | 3 |
| [test_lifecycle_final_state.py](../tests/cases/factor4/test_lifecycle_final_state.py) | 16 | 7 | 5 | 0 | 4 |
| [test_migrated_readonly_scripts.py](../tests/cases/factor4/test_migrated_readonly_scripts.py) | 39 | 18 | 1 | 0 | 20 |
| [test_protocol_deferred_business.py](../tests/cases/factor4/test_protocol_deferred_business.py) | 81 | 42 | 33 | 0 | 6 |
| [test_protocol_read_business.py](../tests/cases/factor4/test_protocol_read_business.py) | 7 | 7 | 0 | 0 | 0 |
| [test_recommendation_business.py](../tests/cases/factor4/test_recommendation_business.py) | 13 | 8 | 0 | 0 | 5 |
| [test_recommendation_replay_business.py](../tests/cases/factor4/test_recommendation_replay_business.py) | 2 | 0 | 0 | 0 | 2 |
| [test_research_search_business.py](../tests/cases/factor4/test_research_search_business.py) | 58 | 43 | 3 | 2 | 10 |
| [test_route_candidate_membership.py](../tests/cases/factor4/test_route_candidate_membership.py) | 6 | 0 | 0 | 0 | 6 |
| [test_route_environment_references.py](../tests/cases/factor4/test_route_environment_references.py) | 1 | 0 | 0 | 1 | 0 |
| [test_run_selection_and_slice_scopes.py](../tests/cases/factor4/test_run_selection_and_slice_scopes.py) | 15 | 12 | 1 | 0 | 2 |
| [test_schema_business.py](../tests/cases/factor4/test_schema_business.py) | 11 | 10 | 1 | 0 | 0 |
| [test_scoring_environment_matrix.py](../tests/cases/factor4/test_scoring_environment_matrix.py) | 14 | 0 | 0 | 0 | 14 |
| [test_summary_business.py](../tests/cases/factor4/test_summary_business.py) | 112 | 93 | 0 | 0 | 19 |
| [test_temporal_final_evidence.py](../tests/cases/factor4/test_temporal_final_evidence.py) | 7 | 7 | 0 | 0 | 0 |
| [test_validity_and_slices_business.py](../tests/cases/factor4/test_validity_and_slices_business.py) | 15 | 13 | 2 | 0 | 0 |
| [test_validity_migration_business.py](../tests/cases/factor4/test_validity_migration_business.py) | 60 | 40 | 13 | 0 | 7 |
| 合计 | 691 | 403 | 84 | 9 | 195 |

