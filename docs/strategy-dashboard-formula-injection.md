# 原始数据到公式结果的自动化执行说明

本专项只针对量化交易/分润平台。原有 175 条主用例和本轮补充 51 条用例保持稳定编号和中文标题；本专项的参数化场景使用独立 `scenario_id`，通过 `case_ids` 关联主用例，不把场景数、单元测试数和真实业务通过数相加。

2026-09-24 更新：当前真实路径已不止原持仓和 G/D 两组。历史权益风险、起终资金差值、首周期收益、组合金额/收益、执行事件、账本和收款登记均已增加真实入口。逐项状态见 [本轮执行状态](strategy-dashboard-formula-expansion-state.md)，公式与不同输入见 [完整矩阵](strategy-dashboard-formula-full-matrix.md)。所有已接通用例均只使用测试配置与本次独立身份。

## 代码入口与证据层级

- `tests/data/strategy_dashboard/formula_finance.json`：逐场景保存业务标题、关联用例、输入、固定预期及允许误差。固定预期不由待测服务返回值生成。
- `tests/data/strategy_dashboard/formula_contract_pending.json`：对待签认公式准备区别性输入，并记录缺少的决策及为何无法唯一判断；这些场景在真实数值验收中明确跳过，不计计算通过。
- `service/strategy_dashboard_formula_oracles.py`：依据业务文档的独立 Decimal 参考计算，用固定预期校验其正确性。它不访问业务库或接口，也不会替产品计算再伪装成接口返回。
- `tests/cases/strategy_dashboard/test_source_position_aggregation.py`：已经接入实际输入表和已部署摘要 API 的持仓聚合用例，保留原始零值、负值、null 和空集合，由后端进行求和与计数。
- `tests/cases/strategy_dashboard/test_source_financial_formulas.py`：已经接入实际账户基础字段和已部署摘要 API 的累计收益率用例，只提交 G/D，不提交收益率结果。
- `tests/cases/strategy_dashboard/test_formula_injection.py`：其余场景的通用执行入口，负责写入、等待计算、读取结果、逐字段断言、清理。当前没有绑定的真实入口仍明确阻断，不表示这些场景已经完成端到端适配。未开启真实模式时标记 `NOT_RUN_LIVE`；缺少已核实入口时标记 `BLOCKED_INJECTION_ENDPOINT`；公式规则未签认时标记 `BLOCKED_CONTRACT`。
- `service/strategy_dashboard_formula_flow.py`：上述流程的通用编排。每轮生成新的请求身份，保存写入收据、计算身份、返回值及清理状态。
- `api/strategy_dashboard_formula_api.py`：预留的 HTTP 适配器。下文是测试侧需要的归一化协议，**不是声明服务端已有这些路由**。真实服务字段不同必须实现映射，不能仅填五个路径就称为绑定完成。

数据库直接造数也可以作为接入方式，但必须先确认表是该公式的真实输入、接口确实消费该表、对象和时间窗口能隔离，并具备按本次唯一身份清理的方式。具体调查记录见 [数据路径调查](strategy-dashboard-formula-data-path.md)。没有要求后端必须新开发五个接口。

已接入路径为 `专用账户 → 原始分钟来源或非分钟业务视图 → 指定 snapshot_id 的真实 analytics/summary 或账单明细 → 固定预期断言 → 清理并重新查询残留`。它验证采集后的输入到公式输出，不验证交易所/NT 采集器，不向生产环境写入。持仓快照中的预存合计故意与逐持仓求和结果不同，累计收益率字段不写入源快照；不能通过预填答案获得通过。

执行入口映射集中保存在 `tests/strategy_dashboard_formula_routes.py`；通用入口自动排除已绑定的场景，同一场景只收集一次。业务 service 不再包含具体 Case ID。使用下述统计命令可以查看每条场景当前是已有真实适配器、缺适配器还是规则待定；该统计不会推断真实执行通过。

## HTTP 适配器的归一化协议

全部路径相对于测试配置的 `api.base_url`，只允许同主机，不允许重定向。以下环境变量均无默认产品路由；应绑定已核实的测试入口或专门实现真实请求/响应映射。

| 环境变量 | 方法 | 用途 |
| --- | --- | --- |
| `QUEST_FORMULA_FIXTURE_PATH` | POST | 创建专用测试对象并写入原始事实 |
| `QUEST_FORMULA_TRIGGER_PATH` | POST | 让真实接入/计算流程消费该批事实 |
| `QUEST_FORMULA_STATUS_PATH` | GET | 含一个 `{run_id}`，查询该次计算完成状态 |
| `QUEST_FORMULA_RESULT_PATH` | GET | 含一个 `{run_id}`，查询对应公式结果 |
| `QUEST_FORMULA_CLEANUP_PATH` | DELETE | 含一个 `{fixture_id}`，清理该批测试对象和事实 |

写入请求只包含 `request_id`、`scenario_id`、`family` 和 `source`，其中 `source` 等于场景的 `inputs`；绝不提交 `expected`、本地计算结果或断言容差。响应 `data` 必须返回同一 `request_id` 和非空 `fixture_id`。应保留原始来源身份，不能把固定场景里的逻辑账号名称直接映射到真实资金账户。

触发请求包含 `request_id` 和 `fixture_id`，响应须返回这两个身份和非空 `run_id`。状态响应返回匹配的 `fixture_id/run_id`，状态可为 `queued`、`running`、`completed`、`failed`、`cancelled`。只有 `completed` 才读取公式结果。

结果响应须返回 `request_id/fixture_id/run_id` 和非空 `result`。`result` 的字段逐一映射到用例约定的逻辑结果，例如 `wallet` 场景中的钱包值；允许额外产品字段。数值按 Decimal 比较，缺字段不能代替显式 null，布尔值不能代替数值，NaN/Infinity 不能通过。

统一 `status=ok/unavailable` 是测试适配层的逻辑含义。产品使用别的字段或逐项状态时，应忠实转换真实状态；不能由本地 Oracle 决定接口是否成功，也不能缺数据时补上预期值。`BLOCKED_CONTRACT` 是用例前置限制，不要求产品返回这个测试字符串。

清理响应为 HTTP 204，或 `data` 中返回匹配的 `fixture_id` 和 `deleted=true`。已知终态后，无论断言成功或失败都清理；清理失败不计通过。写入响应丢失、触发状态不明、后台仍在运行时保留本次恢复身份，避免重复写入或提前删除任务仍在读取的事实。

## 输入的计算层级

“原始”是相对于被测公式的依赖而言，但不能把最终输出写入再读回。例如：

- 钱包由同账户同时间的权益 E、浮盈 U 推导；写入钱包 W 再查询不算验证 M01。
- 成交均价必须提供同一订单的逐笔数量和价格，不能写入 `average_price` 充当原始成交。
- 波动率应从完整权益边界推导日收益后计算；已有上游 Sharpe、净值、回撤不作为这些公式的验证输入。
- 财务的 Gbase/Gclear 可以作为既有基准验证派生算式，但这些标量场景不证明平台会正确建立基准、锁单、收款确认或清算。
- 健康度已批准分项得分可验证权重合成；不能据此声称已验证原始风险指标到分项得分的映射。

真实锁单、后继周期、政策冻结、确认收款、清算和幂等，需要另行绑定真实业务操作。覆盖清单中已单列这些 workflow，不将静态公式样本当成流程通过。尚未签认的公式候选同样单列，不能自行选取一种口径据此报产品 Bug。

## 执行命令

下面命令从项目根目录执行，不打印任何登录或数据库凭据。

```bash
# 独立预期和执行器自身校验：只证明本地代码，零业务写入。
python3 -m pytest tests/unit/test_strategy_dashboard_formula_oracles.py \
  tests/unit/test_strategy_dashboard_formula_flow.py \
  tests/unit/test_strategy_dashboard_source_fixture_flow.py \
  tests/cases/test_http_client.py tests/cases/test_architecture.py \
  -q -p no:cacheprovider

# 查看所有真实公式场景；collect 本身不计通过。
python3 -m pytest tests/cases/strategy_dashboard/ -m strategy_dashboard_formula \
  --collect-only -q -p no:cacheprovider

# 当前已接通的数据库输入路径；只使用 test 配置中的登录和数据库连接。
python3 -m pytest tests/cases/strategy_dashboard/test_source_*.py \
  --live --env test -q -p no:cacheprovider

# 当前场景和适配状态清单：不发 HTTP、不写业务库，也不计业务通过。
python3 -m scripts.report_quant_formula_coverage \
  --output reports/quant-formula-coverage.json \
  --markdown reports/quant-formula-scenarios.md

# 其余场景完成真实入口/字段映射后执行；缺入口明确阻断，不回退本地算法。
python3 -m pytest tests/cases/strategy_dashboard/test_formula_injection.py \
  --live --env test -q -p no:cacheprovider
```

如果本机需要已配置的 HTTP 代理，可在真实 source 命令前设置 `QUEST_FORMULA_HTTP_PROXY=http://127.0.0.1:7890`。这是本机网络配置，不是产品接口条件；不要为了绕过网络错误改用生产环境。

通用 HTTP 运行证据保存在 `reports/quant-formula-injection-<uuid>/`；持仓路径保存在 `reports/quant-raw-position-<uuid>/`；累计收益路径保存在 `reports/quant-financial-source-<scenario-id>-<uuid>.json`。报告分别列出真实通过、真实断言失败、缺入口、缺规则、未运行和资源保留/清理失败；禁止把本地金样本全部通过写成“平台公式全部通过”。
