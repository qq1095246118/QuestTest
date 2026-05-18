# CoinGlass 接口测试用例设计

## 1. 测试范围

测试基准地址：`http://54.168.36.173:9020`

本文件覆盖 `docs/x.json` 中 tag 为 `CoinGlass` 的 6 个接口：

| 接口 | 方法 | 说明 |
|---|---|---|
| `/coinglass/funding-rate/ohlc-history` | GET | 查资金费率 OHLC 历史 |
| `/coinglass/funding-rate/exchange-list` | GET | 查各交易所资金费率列表 |
| `/coinglass/funding-rate/arbitrage` | GET | 查资金费率套利视角数据 |
| `/coinglass/funding-rate/summary` | GET | 查资金费率汇总 |
| `/coinglass/long-short-ratio/history` | GET | 查多空比历史 |
| `/coinglass/controlled_coin_summary` | GET | 查受控币种摘要 |

不设计鉴权用例；服务层接口按无鉴权访问处理。Coinglass 上游历史深度和权限可能影响返回数据量，因此空数组、最早可用时间提示不能直接判为失败。

## 2. 公共测试数据

| 名称 | 值 | 说明 |
|---|---|---|
| `symbol_contract` | `BTCUSDT` | 资金费率和多空比常用合约 symbol |
| `exchange` | `Binance` | 默认交易所 |
| `interval_funding` | `8h` | 资金费率 OHLC 默认周期 |
| `interval_ratio` | `1h` | 多空比默认周期 |
| `limit_small` | `1` | 最小分页边界 |
| `limit_normal` | `10` | 常规小页 |
| `start_time` | `1704067200000` | Unix 毫秒 |
| `end_time` | `1704153600000` | Unix 毫秒 |
| `invalid_symbol` | `NOT_A_SYMBOL` | 非法或无数据 symbol |

## 3. 公共断言规则

| 断言项 | 预期 |
|---|---|
| 响应信封 | 外层包含 `code/status/message/data` |
| 业务成功 | 外层 `code == "200"` 且 `status == "success"` |
| 上游数据 | `data.data` 可以为空数组，但类型必须稳定 |
| 时间字段 | `timestamp` 可以是 ISO8601 字符串；历史点中的 `time/start_time/end_time` 若为数字，应为 13 位毫秒 |
| 数值字段 | 资金费率、价格、多空比例、APR、spread 等字段存在时必须可转数字 |
| limit | 返回数组长度不超过请求 `limit`，除非接口返回聚合对象而非列表 |
| 空窗 | 若响应带 `earliest_available_time_ms`，必须为 13 位毫秒 |
| 错误 | 参数错误不能 500；应通过 HTTP `400/422` 或响应体业务 code 表达 |

## 4. 资金费率 OHLC 历史

接口：`GET /coinglass/funding-rate/ohlc-history`

| Case ID | 类型 | 请求 | 预期断言 |
|---|---|---|---|
| CG-FR-OHLC-NORMAL-001 | Normal | `symbol=BTCUSDT&interval=8h&limit=10` | 成功；`data.symbol/timestamp/data` 存在；`data.data` 为数组 |
| CG-FR-OHLC-BOUNDARY-001 | Boundary | `limit=1` | 返回数据长度不超过 1 |
| CG-FR-OHLC-BOUNDARY-002 | Boundary | 不传参数 | 使用默认 `symbol=BTCUSDT&interval=8h&limit=100` 语义；不能 500 |
| CG-FR-OHLC-PARAM-001 | ParamError | `limit=0` | 返回参数错误 |
| CG-FR-OHLC-PARAM-002 | ParamError | `limit=200001` | 返回参数错误 |
| CG-FR-OHLC-PARAM-003 | ParamError | `interval=bad_interval` | 返回业务错误或空数据提示；不能 500 |
| CG-FR-OHLC-RESPONSE-001 | Response | 正常请求 | OHLC 点若存在，应含 `time/open/high/low/close` 或等价字段 |
| CG-FR-OHLC-DQC-001 | DataQuality | 正常请求 | OHLC 数值可转数字；有 time 时为毫秒或明确时间字符串 |
| CG-FR-OHLC-LOGIC-001 | BusinessLogic | 正常请求 | 若点包含 OHLC，满足 `high >= open/close/low` 且 `low <= open/close/high` |

## 5. 资金费率交易所列表

接口：`GET /coinglass/funding-rate/exchange-list`

| Case ID | 类型 | 请求 | 预期断言 |
|---|---|---|---|
| CG-FR-EXCHANGE-NORMAL-001 | Normal | `symbol=BTCUSDT&limit=10` | 成功；`data.data` 为数组或上游列表结构 |
| CG-FR-EXCHANGE-BOUNDARY-001 | Boundary | `limit=1` | 返回列表长度不超过 1，或聚合结构中列表受限 |
| CG-FR-EXCHANGE-BOUNDARY-002 | Boundary | 不传参数 | 使用默认 `BTCUSDT`；不能 500 |
| CG-FR-EXCHANGE-PARAM-001 | ParamError | `limit=0` | 返回参数错误 |
| CG-FR-EXCHANGE-PARAM-002 | ParamError | `symbol=NOT_A_SYMBOL` | 返回业务错误、空列表或上游提示；不能 500 |
| CG-FR-EXCHANGE-RESPONSE-001 | Response | 正常请求 | 交易所条目存在时包含 `exchange`、`funding_rate` 或稳定的上游字段 |
| CG-FR-EXCHANGE-DQC-001 | DataQuality | 正常请求 | funding rate、next funding time 等字段存在时类型合法 |

## 6. 资金费率套利

接口：`GET /coinglass/funding-rate/arbitrage`

| Case ID | 类型 | 请求 | 预期断言 |
|---|---|---|---|
| CG-FR-ARB-NORMAL-001 | Normal | `symbol=BTCUSDT&limit=10` | 成功；`data.symbol/timestamp/data` 存在 |
| CG-FR-ARB-BOUNDARY-001 | Boundary | `limit=1` | 返回数据不超过 1 条，或聚合对象结构稳定 |
| CG-FR-ARB-PARAM-001 | ParamError | `limit=0` | 返回参数错误 |
| CG-FR-ARB-PARAM-002 | ParamError | `symbol=NOT_A_SYMBOL` | 返回空数据或业务错误；不能 500 |
| CG-FR-ARB-RESPONSE-001 | Response | 正常请求 | 条目存在时至少含 `symbol`；返回的 `buy/sell/apr/funding/fee/spread` 字段必须类型合法 |
| CG-FR-ARB-DQC-001 | DataQuality | 正常请求 | APR、fee、spread、funding 等字段存在时可转数字 |

## 7. 资金费率汇总

接口：`GET /coinglass/funding-rate/summary`

| Case ID | 类型 | 请求 | 预期断言 |
|---|---|---|---|
| CG-FR-SUMMARY-NORMAL-001 | Normal | `symbol=BTCUSDT&limit=10` | 成功；`data.symbol/timestamp/data` 存在；`data.data` 为对象 |
| CG-FR-SUMMARY-BOUNDARY-001 | Boundary | 不传参数 | 使用默认 symbol；不能 500 |
| CG-FR-SUMMARY-PARAM-001 | ParamError | `limit=0` | 返回参数错误 |
| CG-FR-SUMMARY-PARAM-002 | ParamError | `limit=200001` | 返回参数错误 |
| CG-FR-SUMMARY-RESPONSE-001 | Response | 正常请求 | 汇总对象包含资金费率历史、交易所列表、套利或错误提示的稳定字段 |
| CG-FR-SUMMARY-DQC-001 | DataQuality | 正常请求 | 聚合子对象中的时间与数值字段类型合法 |

## 8. 多空比历史

接口：`GET /coinglass/long-short-ratio/history`

| Case ID | 类型 | 请求 | 预期断言 |
|---|---|---|---|
| CG-LS-HISTORY-NORMAL-001 | Normal | `exchange=Binance&symbol=BTCUSDT&interval=1h&limit=10` | 成功；`data.exchange/symbol/interval/data/timestamp` 存在 |
| CG-LS-HISTORY-NORMAL-002 | Normal | 加 `start_time=1704067200000&end_time=1704153600000` | 返回点落在时间窗内，或空窗提示 |
| CG-LS-HISTORY-BOUNDARY-001 | Boundary | `limit=1` | 返回最多 1 条 |
| CG-LS-HISTORY-BOUNDARY-002 | Boundary | 不传 `limit`，只传时间窗 | 服务按时间范围返回；不能 500 |
| CG-LS-HISTORY-PARAM-001 | ParamError | `limit=0` | 返回参数错误 |
| CG-LS-HISTORY-PARAM-002 | ParamError | `end_time <= start_time` | 返回时间窗错误或业务错误 |
| CG-LS-HISTORY-PARAM-003 | ParamError | `exchange=UnknownExchange` | 返回业务错误或空数据；不能 500 |
| CG-LS-HISTORY-RESPONSE-001 | Response | 正常请求 | 点存在时包含多空比例字段，如 `global_account_long_percent/short_percent/long_short_ratio` |
| CG-LS-HISTORY-DQC-001 | DataQuality | 正常请求 | 比例字段可转数字；百分比字段在合理范围内时不超过 100 |
| CG-LS-HISTORY-LOGIC-001 | BusinessLogic | 正常请求 | long/short 百分比存在时二者合计接近 100，允许上游四舍五入误差 |

## 9. 受控币种摘要

接口：`GET /coinglass/controlled_coin_summary`

| Case ID | 类型 | 请求 | 预期断言 |
|---|---|---|---|
| CG-CONTROLLED-SUMMARY-NORMAL-001 | Normal | `symbol=BTCUSDT&exchange=Binance&interval=1h` | 成功；`data.symbol/exchange/interval/base/liquidation` 存在 |
| CG-CONTROLLED-SUMMARY-BOUNDARY-001 | Boundary | 只传必填 `symbol=BTCUSDT` | 使用默认 exchange/interval；不能 500 |
| CG-CONTROLLED-SUMMARY-PARAM-001 | ParamError | 缺少 `symbol` | 返回参数错误，错误信息包含 `symbol` |
| CG-CONTROLLED-SUMMARY-PARAM-002 | ParamError | `interval=bad_interval` | 返回业务错误或明确提示；不能 500 |
| CG-CONTROLLED-SUMMARY-RESPONSE-001 | Response | 正常请求 | `base` 和 `liquidation` 为对象；允许包含上游原始扩展字段 |
| CG-CONTROLLED-SUMMARY-DQC-001 | DataQuality | 正常请求 | 聚合对象中的时间、数量、金额字段存在时类型合法 |

## 10. 自动化落地优先级

| 优先级 | 范围 | 原因 |
|---|---|---|
| P0 | Normal、缺必填、limit 边界、Response | 先锁住接口契约和不 500 |
| P1 | OHLC、多空比、数值类型、空窗 earliest_available_time_ms | 核心数据质量风险 |
| P2 | 上游字段深度断言、Performance | Coinglass 上游返回不稳定，需先积累样本 |
