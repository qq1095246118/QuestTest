# Factor 4.0 使用端场景补充

日期：2026-09-08。范围是最终结果到 MCP 输出及用户连续查询，不启动计算、发布或写数据库。
本次新增 4 个正式业务函数、23 个参数化实例，并增强原有 11 个实例。新增函数全部位于 `tests/cases/factor4/`。

## 六组实现

| 场景 | 正式入口 | 本次数量 | 实际断言 |
| --- | --- | --- | --- |
| 有效性筛选返回成员 | `test_research_search_business.py::test_research_explicit_validity_statistics_include_unknown_catalog_entities` | 增强原 9 实例 | 三种指标形态分别查询 valid/invalid/unknown；核对实际成员、公开指标及有效性 Run、同条件统计，并用实际返回的 ref/Run 回查有效性。无持久化有效性的 unknown 仅该回查步骤不适用。 |
| 多条件研究筛选 | 同文件 `test_research_combined_filters_have_exact_intersection_and_monotone_relaxation` | 新增 9 实例 | 名称、有效性及两个指标门槛的命中、空交集、放宽条件；两个指标各有独立反例。门槛不发送给不支持该参数的 stats。 |
| 母因子聚合搜索及排名 | 同文件 `test_parent_aggregate_research_and_ranking_do_not_substitute_child_results` | 新增 2 实例 | TS/CS 母因子 `child_aggregate` 候选和单侧 raw-signed 排名与 DB 对账；不混入子因子身份或 direct 指标。 |
| 公开范围到选中结果 | `test_consumer_journey_business.py::test_public_scope_selection_replays_selected_result_and_evidence` | 新增 6 实例 | TS 币种、TS 汇总、CS 汇总分别从 search/rank 进入；使用公开 scope、ref、Run 查询指标、有效性、公式及切片，DB 不补填请求身份。 |
| 公开快照到推荐回查 | `test_recommendation_replay_business.py::test_recommended_factors_replay_formula_and_metrics_in_the_same_publication` | 增强原 2 实例 | 先通过 MCP daily 取得实际 forecast；同一 as_of 推荐，再复用已有同批指标及精确 Run 公式回查。DB 独立核对 forecast 和 route。 |
| 六种环境的历史推荐 | `test_recommendation_business.py::test_each_forecast_label_recommends_only_its_visible_historical_routes` | 新增 6 实例 | 每种 label 动态选择真实 forecast/publication 可见交集，核对各 market/profile 的推荐；允许正确空结果，不允许其他环境回退。 |

原 Case 名称、历史需求与 Bug 中文标题不变。母因子搜索/排名不重复验证底层聚合计算。

## 重要边界

- 公开范围链的初始发现不要求 3–20 个因子，也不要求 rank_is_icir 等无关字段非空。
- 显式 Run 的指标接口可以返回多个周期；用独立 DB 全部期次核对数组，再按公开排名 metric_id 选择对应行。研究搜索只有公开摘要无法唯一关联期次时保留契约缺口，不擅自选择最新周期。
- 切片请求使用公开指标的周期及原始 as_of；不从数据库换一个币种或 Run 来补出数据。
- 历史推荐不拿今天的 is_active 排除历史发布路由，但必须有完整 publication_uid/publish_version 证据；不能补造缺失身份。
- 环境指标无 as_of，以显式 batch 和发布指针稳定性核对；历史链不调用无历史查询能力的 current tags。
- 缺公式、有效性、切片或历史环境样本明确记录为缺口；已确认不一致优先于其他缺口，不能被 skip 隐藏。
- 目录/研究分页继续遵守现有预算终止规则，不重开游标绕过限制，不把受限读取称为全集通过。

## 数量口径

| 范围 | 补充前 | 补充后 |
| --- | ---: | ---: |
| 4.0 正式测试函数 | 167 | 171 |
| 全开关参数化实例 | 668 | 691 |
| 默认收集实例（含原暂缓） | 617 | 640 |
| 默认结果级非暂缓实例 | 478 | 501 |

原始计算专项 36、技术专项 15、默认暂缓 139 的范围不变。离线 unit 数量不计入上述业务用例数。

## 执行记录

已执行本次新增/增强的 34 个实例，报告：`reports/factor4-consumer-live-20260908.xml`。
结果为 **34 个 setup error**，全部在共享 MCP initialize 阶段阻断，业务断言未执行。

2026-09-08 17:10（北京时间）的独立 initialize 定向检查确认测试域名返回 HTTP 502，
`error_name=origin_bad_gateway`、`error_category=origin`。客户端将该网关错误正文识别为非 JSON-RPC 信封，
因此 pytest 显示 `initialize response does not declare jsonrpc 2.0`。不能把这 34 个错误统计为 34 个产品 Bug，
也不能由此判断 Token 额度不足。

17:13（北京时间）再次定向检查仍返回相同 HTTP 502，未重复执行已经确定无法握手的业务批次。
随后已成功连接测试数据库，验证新增只读查询：三种指标形态均发现初始 scope，精确 Run 多期次、
公式/有效性证据和研究目录快照查询均执行成功。这只证明 SQL 及样本发现可用，不代表 MCP 输出通过。
其中一个 TS 币种代表未找到对应有效性证据，保留为样本证据观察，不扩大为所有 TS 币种都缺失。

本地收集及离线反例验证与真实环境通过分开记录。最终 Factor 4.0 相关离线测试为 **1522 passed、214 deselected**，
`git diff --check` 通过。离线报告：`reports/factor4-consumer-unit-20260908.xml`。新增用例包含错误 Run、跨类型因子、
错误公式 hash、错误切片币种、合法多周期、公开 period 消歧、缺证据不掩盖错误、历史发布缺身份、
过滤条件被忽略及有效性响应层级错误的离线反例；这些反例不计作真实业务实例。

## 复跑命令

只执行本次 34 个新增/增强实例：

```bash
python3 -u -m pytest \
  tests/cases/factor4/test_consumer_journey_business.py \
  tests/cases/factor4/test_research_search_business.py \
  tests/cases/factor4/test_recommendation_replay_business.py \
  tests/cases/factor4/test_recommendation_business.py \
  -k 'public_scope_selection or explicit_validity_statistics or combined_filters_have or parent_aggregate_research or recommended_factors_replay or each_forecast_label' \
  --live --env test -v --tb=short -rs -o junit_family=xunit1 \
  --junitxml=reports/factor4-consumer-live-rerun.xml
```

全量收集检查不发网络请求：

```bash
python3 -m pytest tests/cases/factor4 --collect-only -qq \
  --include-factor4-internal-calculation --include-factor4-technical --include-factor4-deferred
```
