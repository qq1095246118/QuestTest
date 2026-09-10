# Factor 4.0 现有 Case 问题修正

日期：2026-09-07。处理对象是上一轮审查中“一、现有 Case 的问题”的 12 项，
不是新一轮产品 Bug 验收，也不是另外五组待补业务场景的实现。

2026-09-08 后续已补齐旧目录分页/查询/更新时间/重读的预算判定，以及后续请求阻塞时的已知失败保留。
同时清理重复 Case；最新覆盖数量和定向回归见 [目录修正与用例去重](factor4-catalog-case-cleanup-20260908.md)。
下文原执行记录不覆盖为新结果。

## 逐项处理

| 原问题名称 | 本次处理 | 对应实现 |
| --- | --- | --- |
| 带方向的排名校验 | 从 DB 明确保存的方向证据构建完整候选集，核对 Top/Bottom 数量、成员值和顺序；不再用响应方向证明响应正确。缺方向证据或重叠补位规则时阻断，已确认差异优先失败。 | `service/factor4_summary_service.py` |
| 评分结果对账 | 维度分数列、score_components、payload 中明确保存的分数逐份比较，不再由其中正确的一份掩盖错误副本；分别报告已检查和缺证据部分，部分阻断不能成为整个 Case 通过。 | `service/factor4_scoring_service.py` |
| 评分冻结配置校验 | 传递当前批次冻结阈值；实际有效折数来自每条 metric 的 `oos.valid_fold_count`，不以最低有效折数门槛代替实测值。 | `tests/cases/factor4/test_scoring_environment_matrix.py` |
| 默认最新有效性/Run 选择 | 历史形态单查、批量回查均明确指定 Run。默认 metrics/slice 独立读 completed Run/summary，slice 不依赖完整 validity 且不换 symbol；默认 validity 独立读取完整可见候选，不过滤历史有效形态。排序契约有歧义时阻断。既有省略/null Run 参数矩阵同步修正。 | `service/factor4_run_selection_service.py`、`db/factor4_read_repository.py`、`service/factor4_validity_service.py`、`service/factor4_slice_service.py` |
| forecast 修订切换边界 | 从 DB 全修订按 available_at 独立选择可见 revision，再核对日期、标签、状态和发布时间；推荐 route 使用 DB 预期标签，不允许旧 revision 与旧 route 相互自证。 | `service/factor4_cross_read_service.py` |
| 阻断状态处理 | 排名重复读取发生 publication 切换时不得通过；多分区先汇总已确认失败再处理阻断。Backend 汇总数值已不一致时，缺少 period 时区证据不能把失败转成跳过。 | `tests/cases/factor4/test_final_results.py`、`service/factor4_backend_reconciliation_service.py` |
| 单查与批量详情一致性 | 所有 detail level 检查必要身份，空 data 不能通过；公共字段缺失和值漂移分别检查。definition 未明确必返字段仅在任一端公开时比较，executable 保留原必需字段。 | `service/factor4_read_service.py` |
| 目录状态、分类和统计 | 状态/分类检查在服务端允许的分页链内核对 DB 成员、身份和分类，检查重复及循环游标；仅自然完结时对账完整集合。2026-09-08 按补充文档区分明确预算终止，见下方补充。统计只验证已明确的内部加总，不再宣称已完成实体总数对账。 | `service/factor4_catalog_extension_service.py` |
| 批次完成数量对账 | 对同一只读快照中终态批次的 success、insufficient_sample、failed 指标逐项计数，核对三个 batch 计数字段；未完成或取消批次不强制终态数等于 expected。 | `service/factor4_lifecycle_service.py` |
| 精确公式回查 | get_formula 跟随 evidence 的 calculation_mode、Run 和窗口；不再固定请求 direct，缓存仍包含完整身份。 | `service/factor4_calculation_service.py` |
| 六环境专用样本用例 | 使用全部已发布分区与六标签，不再依赖 profile 名包含 six；复用环境结果对账，摘要为零且实际无 route 合法。缺权重/配对证据单独阻断，不要求每环境强制存在有效 route。 | `tests/cases/factor4/test_scoring_environment_matrix.py` |
| 无关前置依赖 | 纯 DB 费用检查只依赖 live/test/DB 门禁，不登录 Backend 或初始化 MCP；公式身份分支不加载 raw schema，仅 schema 分支按需加载。 | `tests/cases/factor4/test_backend_three_way_business.py`、`tests/cases/factor4/test_formula_closure_business.py` |

## 登记和范围

- 历史 Bug 中文标题未修改，迁移来源记录保留。
- 统计 Case 从 `test_catalog_stats_match_paged_entities` 改名为
  `test_catalog_stats_group_counts_are_internally_consistent`；`service/factor4_case_registry.py` 已同步，避免名称声称超出实际断言的覆盖。
- 原来的重复入口合并、36 个原始计算专项、15 个技术专项和 139 个历史暂缓实例的选择规则不变。
- 本轮增强原 Case；没有新增 4.0 业务测试函数或参数实例。新增离线反例仅验证测试代码本身能识别误报/漏报。
- 最终指标准入/全部拒绝原因、评分缺失值/边界、多分区 MCP 输出矩阵、推荐同版本回查、应有 route 集合这五组原待补场景，没有因此宣称全部完成。

## 仍须明确的边界

- signed 排名只使用 DB 明确保存的 `direction_sign`，不从响应或训练指标符号臆测。历史工具契约明确会逐个排除无法解析方向的因子，因此缺这份证据时，不用原始指标总数强制要求 Top/Bottom 足量；完整方向和集合检查阻断，其他可确认差异及数量上界仍检查。
- Top/Bottom 截止值发生重叠时，若排除重复/补位规则未定义，标记 `BLOCKED_DOC`，不伪造次级排序规则。
- 默认 validity 的完成时间与更新时间选出不同候选，或同一优先时间存在多个候选时，默认选择契约未能明确则 `BLOCKED_DOC`；显式 Run 回查不受此歧义限制。
- 同一 Run 多次发布 validity 时，较新版本发布前一刻允许返回已经可见的旧版本，不能无条件要求空输出。当前行曾被原地更新且缺少可恢复的历史状态时，PIT 边界检查阻断；能够证明首次发布之前才期待空。metrics 在 Run 完成前的不可见检查不变。
- 目录统计接口的过滤后 total 与库成员计数是否同口径尚不明确；自然完结链核对完整成员，受限链只核对返回成员，统计本身目前只证明分组加总自洽。

这些是证据/契约边界，不是本次发现的产品 Bug，也不是可以计为通过的结果。

## 验证

- 全量离线：`python3 -m pytest tests/unit -q --junitxml=reports/factor4-existing-case-corrections-unit-20260907.xml`，1383 passed。
- 实际 pytest 收集：全范围 169 个函数、669 个参数化实例；默认收集 618 个，其中原 139 个暂缓，结果级非暂缓 479 个；51 个专项默认排除。
- 迁移登记、范围选择与 live 门禁相关单测通过；`git diff --check` 通过。
- 未发送真实 MCP/Backend 请求、未访问真实数据库、未启动计算或发布；离线通过不能作为产品验收通过。

## 2026-09-08 目录预算契约补充

来源：[MCP Factor Data 使用说明+测试用例](https://jjp1ynw9z1yy.jp.larksuite.com/wiki/AdOhwoJLMiII4HkCSfJjUB2RpXg)，
已通过登录态浏览器读取第 3.1、3.2、3.3、5.6 节。页面显示 7 月 29 日修改、代码基线核对日期 2026-07-21。
只吸收与本次目录失败相关的规则，不将历史文档中的生产前建议或旧缺口直接登记为当前产品 Bug。

文档将以下限制分开定义，而且说明默认值可以由部署配置覆盖：

- 目录单页最大 50 条。
- 单条 cursor 链累计返回量存在上限；文档默认 200 条，达到后不再提供 next_cursor，并返回预算 warning。DLP-003 明确将此列为预期结果。
- 用户级目录日 points 预算和分钟请求限流是另外两层控制，不能根据一个 bucket 的 remaining 推断分页链还能继续。
- 用户本次补充当前限制为每分钟 2000 条；该口径单独保留，不以旧文档默认值覆盖，也不将它等同于单链累计上限。
- 上轮实测只证明同一链在 2000 条后返回 `CATALOG_CURSOR_BUDGET_REACHED`，没有证明一分钟窗口的计数或重置方式。

测试修正口径：识别精确预算 warning 且 `truncated=true / next_cursor=null` 的受限终态；立即停止该链，不等待重置、重开查询或更换筛选条件来继续枚举。继续核对已经返回的数据，不再把未遍历的 DB 记录当成漏项。
无预算说明的提前结束仍须完整集合对账；warning 与 cursor/truncated 不一致、重复记录、错误身份、字段和分类仍判失败。
受限结果在 JUnit 中明确记录返回数、DB 去重数、终态和完整集合未验收，不计作全库导出通过。

此次仅修正既有目录状态/分类用例及其 Service；不修改通用环境分页器，不新增限流性能或权限专项，也不改变其他 Bug 的状态。

验证已完成：

```bash
python3 -m pytest tests/unit/test_factor4_catalog_extension_service.py -q --junitxml=reports/factor4-catalog-bounded-unit-20260908.xml
python3 -u -m pytest tests/cases/factor4/test_catalog_extension_business.py -k 'every_kind_and_status or each_coin_category' --live --env test -v --tb=short -rs -o junit_family=xunit1 --junitxml=reports/factor4-catalog-bounded-live-20260908.xml
```

- 离线判定器回归 41 passed；其中新增 27 个实例仅测试框架反例，不新增 4.0 live Case。
- 测试环境 14 passed、0 failed、0 skipped、0 errors；2026-09-08 14:48:33 +08:00 开始，耗时 79.561 秒。
- 13 个实例自然完结，JUnit 记录 `COMPLETE_CATALOG_ACCEPTED` 和完整集合验证。
- `new-sub_factor` 一链 40 页返回 2000 个唯一 ID，DB 3421 个；返回项身份、状态、字段和分类对账通过，预算终止后不续取。
- 该实例记录 `BOUNDED_READ_ACCEPTED_NOT_FULL_EXPORT`、`catalog_full_membership_verified=False`、`catalog_budget_code=CATALOG_CURSOR_BUDGET_REACHED`，不会声称完整读取 3421 个因子。
- 本次证明目录单链终止和返回结果验收，不宣称分钟窗口限流、重置时点或其他防导出专项已通过。
- `git diff --check` 通过，报告扫描未发现完整 Token。本次只读 MCP/DB，未修改凭据或服务端数据；历史全量报告保持不变。
