# Open Interest 接口测试用例设计

## 1. 测试范围

测试基准地址：`http://54.168.36.173:9020`

本文件覆盖 `docs/x.json` 中 tag 为 `Open Interest` 的 5 个接口：

| 接口 | 方法 | 说明 |
|---|---|---|
| `/coinglass/oi/history` | GET | 查 OI 历史 |
| `/coinglass/oi/aggregated/history` | GET | 查聚合 OI 历史 |
| `/coinglass/oi/exchanges` | GET | 查 OI 支持的交易所列表 |
| `/coinglass/oi/orderbook/aggregated-history` | GET | 查订单簿聚合历史 |
| `/coinglass/oi/summary` | GET | 查 OI 市场摘要 |

不设计鉴权用例。`force_refresh=true` 会绕过缓存，常规自动化应少用，避免用例慢和上游波动。

## 2. 关键参数边界

| 接口族 | symbol 语义 | 示例 |
|---|---|---|
| `/coinglass/oi/history` | 合约 symbol | `BTCUSDT` |
| `/coinglass/oi/aggregated/history` | 基础币种 | `BTC` |
| `/coinglass/oi/exchanges` | 基础币种 | `BTC` |
| `/coinglass/oi/orderbook/aggregated-history` | 基础币种 | `BTC` |
| `/coinglass/oi/summary` | 基础币种 | `BTC` |

## 3. 公共测试数据

| 名称 | 值 |
|---|---|
| `contract_symbol` | `BTCUSDT` |
| `base_symbol` | `BTC` |
| `wrong_base_symbol` | `BTCUSDT` |
| `exchange` | `Binance` |
| `exchange_list` | `Binance,OKX` |
| `interval_30m` | `30m` |
| `interval_1h` | `1h` |
| `limit_small` | `1` |
| `limit_normal` | `10` |
| `start_time` | `1704067200000` |
| `end_time` | `1704153600000` |
| `unit` | `USD` |
| `range` | `0.3` |

## 4. 公共断言规则

| 断言项 | 预期 |
|---|---|
| 外层信封 | `code/status/message/data` 完整 |
| Coinglass 内层 | `data.code` 成功时可为 `0` 或 `"0"`；`data.msg` 类型稳定 |
| 数据数组 | `data.data` 可为空，但必须是数组或明确为空 |
| earliest_available_time_ms | 存在时必须为 13 位毫秒 |
| OI 数值 | open interest、quantity、usd、change percent 等字段存在时可转数字 |
| 时间窗 | 历史点的时间字段存在时落在 `[start_time, end_time)` |
| 强制刷新 | `force_refresh=true` 只验证契约，不纳入性能基线 |
| 错误 | 错误响应不能 500；应明确指出 symbol、时间窗、limit 或枚举问题 |

## 5. OI 历史

接口：`GET /coinglass/oi/history`

| Case ID | 类型 | 请求 | 预期断言 |
|---|---|---|---|
| OI-HISTORY-NORMAL-001 | Normal | `exchange=Binance&symbol=BTCUSDT&interval=30m&limit=10` | 成功；`data.code/msg/data` 结构稳定 |
| OI-HISTORY-NORMAL-002 | Normal | 加 `start_time=1704067200000&end_time=1704153600000&unit=USD` | 返回点落在时间窗内，或空窗提示 |
| OI-HISTORY-BOUNDARY-001 | Boundary | `limit=1` | 返回最多 1 条 |
| OI-HISTORY-BOUNDARY-002 | Boundary | `force_refresh=false` | 使用缓存语义；成功或明确业务提示 |
| OI-HISTORY-BOUNDARY-003 | Boundary | `force_refresh=true&limit=1` | 绕缓存请求；只校验契约与不 500 |
| OI-HISTORY-PARAM-001 | ParamError | `limit=0` | 返回参数错误 |
| OI-HISTORY-PARAM-002 | ParamError | `end_time <= start_time` | 返回时间窗错误或业务错误 |
| OI-HISTORY-PARAM-003 | ParamError | `symbol=BTC` | 对 history 误传基础币种，应返回空数据或业务提示；不能 500 |
| OI-HISTORY-RESPONSE-001 | Response | 正常请求 | 历史点存在时包含 OI OHLC 或等价字段 |
| OI-HISTORY-DQC-001 | DataQuality | 正常请求 | 时间字段为毫秒；OI 数值字段可转数字 |
| OI-HISTORY-LOGIC-001 | BusinessLogic | 正常请求 | OHLC 形态存在时满足 high/low 关系；时间排序稳定 |

## 6. 聚合 OI 历史

接口：`GET /coinglass/oi/aggregated/history`

| Case ID | 类型 | 请求 | 预期断言 |
|---|---|---|---|
| OI-AGG-HISTORY-NORMAL-001 | Normal | `symbol=BTC&interval=30m&limit=10` | 成功；`data.data` 为数组或空数组 |
| OI-AGG-HISTORY-NORMAL-002 | Normal | `symbol=BTC&start_time=1704067200000&end_time=1704153600000&unit=USD` | 时间窗语义正确 |
| OI-AGG-HISTORY-BOUNDARY-001 | Boundary | `limit=1` | 最多 1 条 |
| OI-AGG-HISTORY-PARAM-001 | ParamError | `limit=0` | 返回参数错误 |
| OI-AGG-HISTORY-PARAM-002 | ParamError | `symbol=BTCUSDT` | 该接口要求基础币种，应返回业务错误或空数据提示 |
| OI-AGG-HISTORY-PARAM-003 | ParamError | `interval=bad_interval` | 返回业务错误或空数据提示；不能 500 |
| OI-AGG-HISTORY-DQC-001 | DataQuality | 正常请求 | 数值字段可转数字；时间字段为毫秒 |
| OI-AGG-HISTORY-LOGIC-001 | BusinessLogic | 正常请求 | 聚合 OI 不应出现负值；若上游字段允许负变化率，仅变化率可为负 |

## 7. OI 交易所列表

接口：`GET /coinglass/oi/exchanges`

| Case ID | 类型 | 请求 | 预期断言 |
|---|---|---|---|
| OI-EXCHANGES-NORMAL-001 | Normal | `symbol=BTC&interval=30m&limit=10` | 成功；交易所列表结构稳定 |
| OI-EXCHANGES-BOUNDARY-001 | Boundary | `limit=1` | 返回最多 1 条或聚合结构受限 |
| OI-EXCHANGES-BOUNDARY-002 | Boundary | `unit=USD` | 单位参数不破坏结构 |
| OI-EXCHANGES-PARAM-001 | ParamError | `limit=0` | 返回参数错误 |
| OI-EXCHANGES-PARAM-002 | ParamError | `symbol=BTCUSDT` | 基础币种接口误传合约 symbol，应返回业务错误或空数据 |
| OI-EXCHANGES-RESPONSE-001 | Response | 正常请求 | item 存在时包含 `exchange/symbol/open_interest_usd/open_interest_quantity` 或等价字段 |
| OI-EXCHANGES-DQC-001 | DataQuality | 正常请求 | OI 数值和变化率字段可转数字 |

## 8. 订单簿聚合历史

接口：`GET /coinglass/oi/orderbook/aggregated-history`

| Case ID | 类型 | 请求 | 预期断言 |
|---|---|---|---|
| OI-ORDERBOOK-NORMAL-001 | Normal | `exchange_list=Binance&symbol=BTC&interval=1h&limit=10` | 成功；`data.symbol/exchange/interval/orderbook` 存在 |
| OI-ORDERBOOK-NORMAL-002 | Normal | `exchange_list=Binance,OKX&symbol=BTC&range=0.3` | 多交易所参数可接受；结构稳定 |
| OI-ORDERBOOK-BOUNDARY-001 | Boundary | `limit=1` | 返回最多 1 条或 orderbook 内部列表受限 |
| OI-ORDERBOOK-BOUNDARY-002 | Boundary | `range=0` | 合法最小深度边界或返回明确业务提示 |
| OI-ORDERBOOK-PARAM-001 | ParamError | `limit=0` | 返回参数错误 |
| OI-ORDERBOOK-PARAM-002 | ParamError | `range=-0.1` | 返回参数错误 |
| OI-ORDERBOOK-PARAM-003 | ParamError | `end_time <= start_time` | 返回时间窗错误 |
| OI-ORDERBOOK-RESPONSE-001 | Response | 正常请求 | `orderbook` 为对象或数组；包含 bid/ask 聚合字段时类型合法 |
| OI-ORDERBOOK-DQC-001 | DataQuality | 正常请求 | aggregated bids/asks 金额和数量字段可转数字，且不为负 |

## 9. OI 市场摘要

接口：`GET /coinglass/oi/summary`

| Case ID | 类型 | 请求 | 预期断言 |
|---|---|---|---|
| OI-SUMMARY-NORMAL-001 | Normal | `symbol=BTC&exchange=Binance&interval=1h&limit=1` | 成功；市场摘要结构稳定 |
| OI-SUMMARY-BOUNDARY-001 | Boundary | 不传参数 | 使用默认 `symbol=BTC&exchange=Binance&interval=1h&limit=1` 语义 |
| OI-SUMMARY-BOUNDARY-002 | Boundary | `limit=1` | 返回最新 1 条或聚合摘要 |
| OI-SUMMARY-PARAM-001 | ParamError | `limit=0` | 返回参数错误 |
| OI-SUMMARY-PARAM-002 | ParamError | `symbol=BTCUSDT` | 基础币种接口误传合约 symbol，应返回业务错误或空数据 |
| OI-SUMMARY-RESPONSE-001 | Response | 正常请求 | `data.symbol/exchange/interval/limit` 回显；返回的 `orderbook/longshort/open_interest/whale_flow_spikes` 子对象必须类型合法 |
| OI-SUMMARY-DQC-001 | DataQuality | 正常请求 | 聚合子对象中的时间、金额、数量字段类型合法 |

## 10. 自动化落地优先级

| 优先级 | 范围 | 原因 |
|---|---|---|
| P0 | Normal、limit 边界、symbol 语义错误、Response | 先锁住 OI 接口最容易错的 BTC/BTCUSDT 边界 |
| P1 | 时间窗、unit、range、force_refresh=false、DataQuality | 覆盖数据质量与查询语义 |
| P2 | force_refresh=true、Performance、深层 orderbook 字段 | 上游波动大，适合非阻塞或单独标记 |
