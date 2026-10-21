# 三个页面真实接口自动化复审

## 结论

当前不能认定账户管理、账户详情、策略详情已经覆盖完整。仓库当前收集 49 条真实接口 Case：账号管理 14 条、账户详情 10 条、策略详情 25 条。它们验证真实页面请求、真实主体发现、`测试库 fixture → read view/API → 响应断言 → 精确清理` 链路；设计清单中的部分深层数据场景仍未落到可执行接口 Case。

## 覆盖统计

| 模块 | 设计清单 | 当前 live Case | 已覆盖范围 | 主要缺口 |
|---|---:|---:|---|---|
| 账号管理 | 页面总览字段和聚合场景 | 14 | 16 个核心指标请求、状态与原因码、空集合、读上限、分页/交易所/状态/账号筛选、多账号 fixture 对账 | 年化、回撤、Sharpe、Sortino、Calmar、波动率、胜率、盈亏比、generation/迟到修订 |
| 账户详情 | 314 条（分润 57、真实净值 257） | 10 | N01 持仓摘要、16 个核心指标状态、正向/0/缺失、日期窗口、快照追踪、缺失账号和反向区间 | 保证金/仓位比例、N02/N04/N09/N13-N23/N30/N32、分润和资金流 |
| 策略详情 | 139 条（SD/CALC/RES） | 25 | 真实策略发现、summary、definition/accounts、risk/series、positions/signals/batches/plans/intents/orders/fills 无 snapshot 资源入口 | 当前/历史两个 Tab、8 个核心指标完整对账、切换区间、多账号归属、逐资源字段/父子 ID、分页、代次、跨域对账 |

## 链路审查

1. API 层只负责请求封装，当前三个模块均使用真实 HTTP 客户端。
2. DB 层会创建带测试前缀的账户、策略、批次和 read view，并在 `finally` 清理。
3. 现有 fixture 仍是直接写入 `quant_read_views` 和手工 `quant_collection_batches`，没有调用或验证真实 collector 发布的 batch/manifest。因此当前应称为“真实 API + read-view fixture”，不能称为“真实 collector 全链路”。
4. 未传 `--live --env test` 时三个目录只会门禁跳过；显式 live 运行时，fixture 未被后端消费会以明确的 `SKIPPED/BLOCKED_CONTRACT` 记录，并保留 `SNAPSHOT_EXPIRED`、`MINUTE_VIEW_UNAVAILABLE` 或 `SUBJECT_NOT_FOUND` 诊断，不再把它们误报为接口失败。
5. 复现页面默认请求时，账号管理目录和 portfolio summary 均返回 HTTP 200、`success=true`、`mock=false`；在读视图上限触发时，`snapshot_id` 可以为空并通过 `READ_LIMIT_EXCEEDED` 说明原因。账户详情不带 `snapshot_id` 的 N01 请求也返回真实生成的 snapshot。说明接口本身可用，失败集中在脚本自造 snapshot 的 fixture 分支。

## 断言审查

- 账号管理：已补充 portfolio 身份、快照、mock 标识、金额逐项、分页和交易所过滤；仍缺少年化/风险指标、来源状态、原因码、公式版本、空集合和目录逐行字段对账。
- 账户详情：已断言 success、mock、快照、账号身份、持仓摘要和核心金额字段正向/0/缺失；仍缺少保证金使用率、时间窗口和其它资源端点。
- 策略详情：已补充 definition/accounts、risk/series 和交易资源入口，并移除字段缺失时回退预期值的弱断言；仍缺少逐资源字段、父子 ID、当前/历史区间和完整核心指标对账。
- 清理：账号管理快照残留检查已改为按实际 `account_id + source_id` 校验；策略清理增加 read page、batch、definition、run 和 NT 绑定残留检查。

## 修复后的验证结果

真实页面请求用例使用不带 `snapshot_id` 的请求，已验证 HTTP 200、`success=true`、`mock=false`；认证登录也增加了幂等重试并有独立协议测试。直接写入 `quant_read_views` 的旧 fixture 仍未接入后端 collector，因此只会跳过；这部分在 collector 发布真实 generation/manifest 后才会恢复数值断言。

策略详情资源的请求参数也已按端点拆分：交易资源不再携带不支持的 `scope`，曲线资源补充必填的 `subject_type` 和 `types=nav,drawdown`。

## 验证命令

```bash
pytest --collect-only -q -m 'account_management or account_detail or strategy_detail'
pytest -q
pytest -q --live --env test tests/cases/account_management tests/cases/account_detail tests/cases/strategy_detail
```

默认全量回归用于验证框架完整性；只有显式 live 命令的结果，才可作为页面真实数据覆盖结论。当前 live 运行被后端 source/read-view 契约阻断，不能把阻断计为业务通过。
