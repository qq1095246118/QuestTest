# binance-usdm 接口测试用例设计

## 1. 测试范围

测试基准地址：`http://54.168.36.173:9020`

本文件覆盖 `docs/x.json` 中 tag 为 `binance-usdm` 的 2 个接口：

| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/usdm/volume-rank` | GET | U 本位成交量排行 |
| `/api/usdm/top-gainers` | GET | U 本位涨幅榜 |

不设计鉴权用例。该类接口是内部聚合排行，不属于 `binance-full` raw 明细接口，因此重点是排行排序、count、历史数组长度和参数边界。

## 2. 公共断言规则

| 断言项 | 预期 |
|---|---|
| 响应信封 | `code/status/message/data` 完整 |
| 业务成功 | `code == "200"` 且 `status == "success"` |
| count | `data.count == len(data.items)`，除非接口明确返回截断统计 |
| symbol | item 中 `symbol` 为非空字符串 |
| 排序 | 排行字段应按接口说明排序 |
| 历史数组 | `history_m_days` 或日线历史数组长度不超过请求参数 |
| 数值字段 | volume、change、ticker 等指标存在时可转数字 |
| 错误 | 参数错误不能 500；应明确指出非法参数 |

## 3. `/api/usdm/volume-rank`

### 3.1 参数边界

| 参数 | 合法范围或默认 |
|---|---|
| `range_unit` | `hours` 或 `days` |
| `n` | `1..168` |
| `top_k` | `1..200` |
| `use_quote_volume` | boolean，默认 `true` |
| `m_days` | `1..90` |
| `include_ticker_24h` | boolean，默认 `true` |

### 3.2 用例矩阵

| Case ID | 类型 | 请求 | 预期断言 |
|---|---|---|---|
| BUSDM-VOLUME-NORMAL-001 | Normal | `range_unit=hours&n=24&top_k=10&use_quote_volume=true&m_days=7&include_ticker_24h=true` | 成功；`data.now_ms/range_unit/n/top_k/m_days/count/items` 存在 |
| BUSDM-VOLUME-NORMAL-002 | Normal | `range_unit=days&n=7&top_k=10` | 成功；range_unit 回显为 `days` |
| BUSDM-VOLUME-BOUNDARY-001 | Boundary | `n=1&top_k=1&m_days=1` | 最小边界成功；items 最多 1 条 |
| BUSDM-VOLUME-BOUNDARY-002 | Boundary | `n=168&top_k=200&m_days=90` | 最大边界成功或在性能上可接受；不能 500 |
| BUSDM-VOLUME-BOUNDARY-003 | Boundary | `use_quote_volume=false` | 排名按基础资产成交量语义；结构稳定 |
| BUSDM-VOLUME-BOUNDARY-004 | Boundary | `include_ticker_24h=false` | item 可不含 `ticker_24h` 或该字段为空；其他字段稳定 |
| BUSDM-VOLUME-PARAM-001 | ParamError | `range_unit=weeks` | 返回枚举或业务参数错误 |
| BUSDM-VOLUME-PARAM-002 | ParamError | `n=0` | 返回参数错误 |
| BUSDM-VOLUME-PARAM-003 | ParamError | `n=169` | 返回参数错误 |
| BUSDM-VOLUME-PARAM-004 | ParamError | `top_k=0` 或 `top_k=201` | 返回参数错误 |
| BUSDM-VOLUME-PARAM-005 | ParamError | `m_days=0` 或 `m_days=91` | 返回参数错误 |
| BUSDM-VOLUME-RESPONSE-001 | Response | 正常请求 | 每个 item 至少含 `symbol/range_unit/n`，常见含 `volume/ticker_as_of/ticker_24h/history_m_days` |
| BUSDM-VOLUME-DQC-001 | DataQuality | 正常请求 | `now_ms/ticker_as_of` 为 13 位毫秒；volume 可转数字 |
| BUSDM-VOLUME-LOGIC-001 | BusinessLogic | 正常请求 | items 按 volume 或 quote volume 降序；`history_m_days.length <= m_days` |
| BUSDM-VOLUME-PERF-001 | Performance | `top_k=10&m_days=7` | 响应时间小于 2 秒 |

## 4. `/api/usdm/top-gainers`

### 4.1 参数边界

| 参数 | 合法范围或默认 |
|---|---|
| `change_threshold` | number，默认 `5.0` |
| `days_history` | `1..60` |
| `limit` | `1..200000` 或空 |

### 4.2 用例矩阵

| Case ID | 类型 | 请求 | 预期断言 |
|---|---|---|---|
| BUSDM-GAINERS-NORMAL-001 | Normal | `change_threshold=5&days_history=10&limit=10` | 成功；`data.change_threshold/days_history/limit/count/sort_by/items` 存在 |
| BUSDM-GAINERS-BOUNDARY-001 | Boundary | `change_threshold=0&days_history=1&limit=1` | 最小边界成功；items 最多 1 条 |
| BUSDM-GAINERS-BOUNDARY-002 | Boundary | `days_history=60` | 最大历史天数成功或返回明确业务提示；不能 500 |
| BUSDM-GAINERS-BOUNDARY-003 | Boundary | 不传 `limit` | 返回全量或默认范围；结构稳定 |
| BUSDM-GAINERS-BOUNDARY-004 | Boundary | `change_threshold=1000` | 可返回空数组；不能 500 |
| BUSDM-GAINERS-PARAM-001 | ParamError | `days_history=0` | 返回参数错误 |
| BUSDM-GAINERS-PARAM-002 | ParamError | `days_history=61` | 返回参数错误 |
| BUSDM-GAINERS-PARAM-003 | ParamError | `limit=0` | 返回参数错误 |
| BUSDM-GAINERS-PARAM-004 | ParamError | `change_threshold=not_number` | 返回参数类型错误 |
| BUSDM-GAINERS-RESPONSE-001 | Response | 正常请求 | item 存在时至少含 `symbol`；返回的涨幅、价格、成交量和历史字段必须类型合法 |
| BUSDM-GAINERS-DQC-001 | DataQuality | 正常请求 | 涨跌幅、价格、成交量等字段存在时可转数字 |
| BUSDM-GAINERS-LOGIC-001 | BusinessLogic | 正常请求 | items 按 `sort_by` 指定字段排序；涨幅不低于 `change_threshold`，除非字段缺失时按接口实际行为记录 |
| BUSDM-GAINERS-PERF-001 | Performance | `limit=10&days_history=10` | 响应时间小于 2 秒 |

## 5. 自动化落地优先级

| 优先级 | 范围 | 原因 |
|---|---|---|
| P0 | Normal、参数上下界、Response | 排行接口参数边界清晰，适合先自动化 |
| P1 | 排序、count、历史数组长度、时间戳 | 验证聚合语义是否可信 |
| P2 | 最大边界、Performance | 大范围查询可能受数据量影响，适合独立标记 |
