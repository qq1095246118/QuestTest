# binance-full 接口测试用例设计

## 1. 测试范围

测试基准地址：`http://54.168.36.173:9020`

本文件覆盖 `docs/x.json` 中 tag 为 `binance-full` 的 20 个接口。该类接口用于 Binance 全量 raw、registry、USDM、COIN-M、交割连续、Funding 和批量边界查询。

不设计鉴权用例；这些接口均按无鉴权访问处理。

## 2. 子域与参数边界

| 子域 | 接口特征 | 主标识 | 关键边界 |
|---|---|---|---|
| 元数据 | `/meta/tables`、registry、complete/delisted symbols | 无或 `symbol` | count 与 items 一致，过滤条件回显 |
| USDM | `/api/binance-full/usdm/...` | `symbol=BTCUSDT` | 支持逗号多选；可含 `include_legacy_coinm_in_usdm_aggregate` |
| COIN-M PERP | `/coinm-perp/...` | `pair=BTCUSD` | `contract_type=PERPETUAL` 必填 |
| COIN-M delivery | `/coinm-delivery/...` | `pair=BTCUSD` | `contract_type` 仅 `CURRENT_QUARTER/NEXT_QUARTER` |
| USDM delivery | `/usdm-delivery/...` | `pair=BTCUSDT` | `contract_type` 仅 `CURRENT_QUARTER/NEXT_QUARTER` |
| 批量边界 | `POST ...time-bounds` | `symbols` | 支持 JSON 数组或逗号字符串 |

## 3. 公共测试数据

| 名称 | 值 |
|---|---|
| `usdm_symbol` | `BTCUSDT` |
| `usdm_symbols_multi` | `BTCUSDT,ETHUSDT` |
| `coinm_pair` | `BTCUSD` |
| `coinm_pairs_multi` | `BTCUSD,ETHUSD` |
| `usdm_delivery_pair` | `BTCUSDT` |
| `interval_1m` | `1m` |
| `interval_1h` | `1h` |
| `start_time_ms` | `1704067200000` |
| `end_time_ms` | `1704153600000` |
| `limit_small` | `1` |
| `limit_normal` | `10` |
| `limit_max_detail` | `200000` |
| `limit_max_symbol_list` | `20000` |

## 4. 公共断言规则

| 响应类型 | 核心断言 |
|---|---|
| 通用信封 | `code/status/message/data` 完整；业务成功为 `code == "200"` 且 `status == "success"` |
| 列表响应 | `data.filters` 存在；`items.length <= limit`；`pagination` 与请求一致 |
| 多 symbol/pair 响应 | `data.multi=true`；`data.by_symbol` 存在；key 与请求的 symbol/pair 映射一致 |
| 时间边界响应 | `kline/funding` 或 `by_symbol[*].kline/funding` 含 `time_field/min_time_ms/max_time_ms/has_data` |
| K 线 item | `timestamp/open/high/low/close/volume` 存在；OHLC 合法；时间戳为 13 位毫秒 |
| Funding item | `funding_rate/funding_time/mark_price` 存在时可转数字或毫秒时间戳 |
| 批量边界 item | 包含 `symbol` 或 `pair`、`min_time_ms`、`max_time_ms`、`has_data`；有数据时 `min <= max` |
| 参数错误 | HTTP `400/422` 或响应体 `code=400/422`；错误信息指向缺失或非法参数 |

## 5. 元数据接口

### 5.1 `/api/binance-full/meta/tables`

| Case ID | 类型 | 请求 | 预期断言 |
|---|---|---|---|
| BF-META-TABLES-NORMAL-001 | Normal | `GET /api/binance-full/meta/tables` | 成功；`data.capabilities` 为对象 |
| BF-META-TABLES-RESPONSE-001 | Response | 正常请求 | capabilities 至少包含 Binance full 相关命名空间或能力描述；不要求固定顺序 |
| BF-META-TABLES-PERF-001 | Performance | 正常请求 | 响应时间小于 1 秒 |

### 5.2 `/api/binance-full/usdm/registry/symbols`

| Case ID | 类型 | 请求 | 预期断言 |
|---|---|---|---|
| BF-REGISTRY-NORMAL-001 | Normal | `contract_type=PERPETUAL&quote_asset=USDT&status=TRADING` | 成功；`data.filters/count/items` 存在；items 为合约目录行 |
| BF-REGISTRY-BOUNDARY-001 | Boundary | 不传任何过滤参数 | 返回全量或默认范围；`count == len(items)` |
| BF-REGISTRY-BOUNDARY-002 | Boundary | `status=TRADING,CLOSE` | 支持逗号多状态；items 的 status 在请求集合内或返回空 |
| BF-REGISTRY-PARAM-001 | ParamError | `contract_type=INVALID` | 返回参数错误或业务错误；不能 500 |
| BF-REGISTRY-RESPONSE-001 | Response | 正常请求 | 每个 item 至少含 `symbol`，可选含 `status/contract_type/quote_asset/margin_asset/is_enabled/onboard_date_ms` |
| BF-REGISTRY-DQC-001 | DataQuality | 正常请求 | `onboard_date_ms` 非空时为 13 位毫秒 |

### 5.3 `/api/binance-full/usdm/meta/complete-symbols`

| Case ID | 类型 | 请求 | 预期断言 |
|---|---|---|---|
| BF-COMPLETE-NORMAL-001 | Normal | `start_time_ms=1704067200000&end_time_ms=1704153600000&limit=10` | 成功；`items` 为 symbol 字符串数组；`count == len(items)` |
| BF-COMPLETE-BOUNDARY-001 | Boundary | `limit=1` | 最多返回 1 个 symbol |
| BF-COMPLETE-BOUNDARY-002 | Boundary | `include_legacy_coinm_in_usdm_aggregate=true` | 成功或明确业务提示；不能 500 |
| BF-COMPLETE-PARAM-001 | ParamError | 只传 `start_time_ms` | 返回时间窗成对错误 |
| BF-COMPLETE-PARAM-002 | ParamError | `limit=0` | 返回参数错误 |
| BF-COMPLETE-DQC-001 | DataQuality | 正常请求 | 所有 items 为非空大写字符串 |

### 5.4 `/api/binance-full/usdm/meta/delisted-symbols`

| Case ID | 类型 | 请求 | 预期断言 |
|---|---|---|---|
| BF-DELISTED-NORMAL-001 | Normal | `status=CLOSE,OFF_EXCHANGE&limit=10` | 成功；items 为已下架 symbol 字符串数组 |
| BF-DELISTED-BOUNDARY-001 | Boundary | 不传 `status` | 使用默认下架状态语义 |
| BF-DELISTED-BOUNDARY-002 | Boundary | `include_disabled_only=true` | 返回禁用合约集合或空数组；不能 500 |
| BF-DELISTED-PARAM-001 | ParamError | `status=INVALID_STATUS` | 返回参数错误或业务错误 |
| BF-DELISTED-PARAM-002 | ParamError | `limit=20001` | 返回参数错误 |

## 6. 时间边界接口

### 6.1 USDM 时间边界

接口：`GET /api/binance-full/usdm/meta/time-range`

| Case ID | 类型 | 请求 | 预期断言 |
|---|---|---|---|
| BF-USDM-TIMERANGE-NORMAL-001 | Normal | `symbol=BTCUSDT&interval=1m` | 单 symbol 成功；`data.kline` 与 `data.funding` 结构完整 |
| BF-USDM-TIMERANGE-NORMAL-002 | Normal | `symbol=BTCUSDT,ETHUSDT&interval=1m` | 多 symbol 成功；`data.multi=true`；`by_symbol` 含请求 symbol |
| BF-USDM-TIMERANGE-BOUNDARY-001 | Boundary | `interval=1h` | 查询 1h 专表语义；有数据时边界毫秒合法 |
| BF-USDM-TIMERANGE-PARAM-001 | ParamError | 缺少 `symbol` | 返回参数错误 |
| BF-USDM-TIMERANGE-PARAM-002 | ParamError | `symbol=BTCUSDT&interval=99m` | 返回无数据或业务提示；不能 500 |

### 6.2 COIN-M PERP 时间边界

接口：`GET /api/binance-full/coinm-perp/meta/time-range`

| Case ID | 类型 | 请求 | 预期断言 |
|---|---|---|---|
| BF-COINM-PERP-TIMERANGE-NORMAL-001 | Normal | `pair=BTCUSD&contract_type=PERPETUAL&interval=1m` | 成功；`kline` 和 `funding` 时间边界结构完整 |
| BF-COINM-PERP-TIMERANGE-NORMAL-002 | Normal | `pair=BTCUSD,ETHUSD&contract_type=PERPETUAL` | 多 pair 返回 `by_symbol` 或等价分桶结构 |
| BF-COINM-PERP-TIMERANGE-PARAM-001 | ParamError | 缺少 `pair` | 返回参数错误 |
| BF-COINM-PERP-TIMERANGE-PARAM-002 | ParamError | 缺少 `contract_type` | 返回参数错误 |
| BF-COINM-PERP-TIMERANGE-PARAM-003 | ParamError | `contract_type=CURRENT_QUARTER` | 返回参数错误或业务错误；PERP 仅允许 `PERPETUAL` |

### 6.3 COIN-M delivery 时间边界

接口：`GET /api/binance-full/coinm-delivery/meta/time-range`

| Case ID | 类型 | 请求 | 预期断言 |
|---|---|---|---|
| BF-COINM-DELIVERY-TIMERANGE-NORMAL-001 | Normal | `pair=BTCUSD&contract_type=CURRENT_QUARTER&interval=1m` | 成功；仅要求 `kline` 时间边界，不要求 funding |
| BF-COINM-DELIVERY-TIMERANGE-BOUNDARY-001 | Boundary | `contract_type=NEXT_QUARTER` | 合法枚举；成功或无数据提示 |
| BF-COINM-DELIVERY-TIMERANGE-PARAM-001 | ParamError | 缺少 `contract_type` | 返回参数错误 |
| BF-COINM-DELIVERY-TIMERANGE-PARAM-002 | ParamError | `contract_type=PERPETUAL` | 返回参数错误 |

### 6.4 USDM delivery 时间边界

接口：`GET /api/binance-full/usdm-delivery/meta/time-range`

| Case ID | 类型 | 请求 | 预期断言 |
|---|---|---|---|
| BF-USDM-DELIVERY-TIMERANGE-NORMAL-001 | Normal | `pair=BTCUSDT&contract_type=CURRENT_QUARTER&interval=1m` | 成功；`kline` 时间边界结构完整 |
| BF-USDM-DELIVERY-TIMERANGE-BOUNDARY-001 | Boundary | `contract_type=NEXT_QUARTER` | 合法枚举；成功或无数据提示 |
| BF-USDM-DELIVERY-TIMERANGE-PARAM-001 | ParamError | 缺少 `pair` | 返回参数错误 |
| BF-USDM-DELIVERY-TIMERANGE-PARAM-002 | ParamError | `contract_type=PERPETUAL` | 返回参数错误 |

## 7. K 线明细接口

### 7.1 `/api/binance-full/usdm/kline`

| Case ID | 类型 | 请求 | 预期断言 |
|---|---|---|---|
| BF-USDM-KLINE-NORMAL-001 | Normal | `symbol=BTCUSDT&interval=1m&start_time_ms=1704067200000&end_time_ms=1704153600000&limit=10` | 成功；items 为 K 线行；分页正确 |
| BF-USDM-KLINE-NORMAL-002 | Normal | `symbol=BTCUSDT,ETHUSDT&interval=1m&limit=10` | 多 symbol 分桶；每个桶 items 不超过 limit |
| BF-USDM-KLINE-BOUNDARY-001 | Boundary | `interval=1h&limit=1` | 查询 1h 表语义；最多 1 条 |
| BF-USDM-KLINE-BOUNDARY-002 | Boundary | `include_total=true&limit=1` | `pagination.total` 若返回则为非负整数 |
| BF-USDM-KLINE-PARAM-001 | ParamError | 缺少 `symbol` | 返回参数错误 |
| BF-USDM-KLINE-PARAM-002 | ParamError | 只传时间窗一端 | 返回时间窗成对错误 |
| BF-USDM-KLINE-PARAM-003 | ParamError | `limit=0` 或 `limit=200001` | 返回参数错误 |
| BF-USDM-KLINE-DQC-001 | DataQuality | 正常请求 | timestamp 毫秒；数值字段可转数字 |
| BF-USDM-KLINE-LOGIC-001 | BusinessLogic | 正常请求 | OHLC 合法；timestamp 在窗口内 |

### 7.2 `/api/binance-full/usdm/kline-1h/all-symbols`

| Case ID | 类型 | 请求 | 预期断言 |
|---|---|---|---|
| BF-USDM-1H-ALL-NORMAL-001 | Normal | `start_time_ms=1704067200000&end_time_ms=1704153600000&order=time_asc` | 成功；`data.items` 为扁平 K 线数组；`count == len(items)` |
| BF-USDM-1H-ALL-NORMAL-002 | Normal | 加 `symbol=BTCUSDT` | 只返回该 symbol 或空数组 |
| BF-USDM-1H-ALL-BOUNDARY-001 | Boundary | `order=time_desc` | 返回按时间倒序或服务明确提示 |
| BF-USDM-1H-ALL-PARAM-001 | ParamError | 缺少 `start_time_ms` | 返回参数错误 |
| BF-USDM-1H-ALL-PARAM-002 | ParamError | 缺少 `end_time_ms` | 返回参数错误 |
| BF-USDM-1H-ALL-PARAM-003 | ParamError | `symbol=BTCUSDT,ETHUSDT` | 文档说明不支持多选；应返回参数错误或业务错误 |
| BF-USDM-1H-ALL-DQC-001 | DataQuality | 正常请求 | timestamp 毫秒；interval 应为 `1h` 或符合接口语义 |
| BF-USDM-1H-ALL-LOGIC-001 | BusinessLogic | 正常请求 | OHLC 合法；排序方向正确 |

### 7.3 COIN-M / delivery K 线明细

覆盖接口：

| 接口 | 合法主参数 |
|---|---|
| `/api/binance-full/coinm-perp/kline` | `pair=BTCUSD&contract_type=PERPETUAL` |
| `/api/binance-full/coinm-delivery/kline` | `pair=BTCUSD&contract_type=CURRENT_QUARTER` |
| `/api/binance-full/usdm-delivery/kline` | `pair=BTCUSDT&contract_type=CURRENT_QUARTER` |

| Case ID | 类型 | 适用接口 | 请求/变体 | 预期断言 |
|---|---|---|---|---|
| BF-DERIV-KLINE-NORMAL-001 | Normal | 三个接口 | 合法 `pair/contract_type/interval=1m/limit=10` | 成功；items 为 K 线行；分页正确 |
| BF-DERIV-KLINE-NORMAL-002 | Normal | 三个接口 | 多 pair 逗号分隔 | 多桶结构正确或返回明确不支持提示；不能 500 |
| BF-DERIV-KLINE-BOUNDARY-001 | Boundary | 三个接口 | `limit=1&offset=0` | 最多 1 条；分页回显正确 |
| BF-DERIV-KLINE-BOUNDARY-002 | Boundary | 三个接口 | `include_total=true` | total 若返回则为非负整数 |
| BF-DERIV-KLINE-PARAM-001 | ParamError | 三个接口 | 缺少 `pair` | 返回参数错误 |
| BF-DERIV-KLINE-PARAM-002 | ParamError | 三个接口 | 缺少 `contract_type` | 返回参数错误 |
| BF-DERIV-KLINE-PARAM-003 | ParamError | PERP 接口 | `contract_type=CURRENT_QUARTER` | 返回错误；PERP 仅 `PERPETUAL` |
| BF-DERIV-KLINE-PARAM-004 | ParamError | delivery 接口 | `contract_type=PERPETUAL` | 返回错误；delivery 仅季度枚举 |
| BF-DERIV-KLINE-DQC-001 | DataQuality | 三个接口 | 正常请求 | timestamp 毫秒；数值字段可转数字 |
| BF-DERIV-KLINE-LOGIC-001 | BusinessLogic | 三个接口 | 正常请求 | OHLC 合法；时间窗过滤正确 |

## 8. Funding 明细接口

覆盖接口：

| 接口 | 合法主参数 |
|---|---|
| `/api/binance-full/usdm/funding` | `symbol=BTCUSDT` |
| `/api/binance-full/coinm-perp/funding` | `pair=BTCUSD&contract_type=PERPETUAL` |

| Case ID | 类型 | 适用接口 | 请求/变体 | 预期断言 |
|---|---|---|---|---|
| BF-FUNDING-NORMAL-001 | Normal | 两个接口 | 合法主参数 + `start_time_ms/end_time_ms/limit=10` | 成功；items 为 funding 行 |
| BF-FUNDING-NORMAL-002 | Normal | 两个接口 | 多 symbol/pair 逗号分隔 | 多桶结构正确 |
| BF-FUNDING-BOUNDARY-001 | Boundary | 两个接口 | `limit=1` | 最多 1 条 |
| BF-FUNDING-BOUNDARY-002 | Boundary | USDM | `include_legacy_coinm_in_usdm_aggregate=true` | 成功或明确业务提示；不能 500 |
| BF-FUNDING-PARAM-001 | ParamError | USDM | 缺少 `symbol` | 返回参数错误 |
| BF-FUNDING-PARAM-002 | ParamError | COIN-M PERP | 缺少 `pair` | 返回参数错误 |
| BF-FUNDING-PARAM-003 | ParamError | COIN-M PERP | 缺少 `contract_type` | 返回参数错误 |
| BF-FUNDING-PARAM-004 | ParamError | 两个接口 | `end_time_ms <= start_time_ms` | 返回时间窗错误 |
| BF-FUNDING-DQC-001 | DataQuality | 两个接口 | 正常请求 | `funding_time` 为 13 位毫秒；`funding_rate/mark_price` 可转数字 |
| BF-FUNDING-LOGIC-001 | BusinessLogic | 两个接口 | 正常请求 | funding_time 在请求窗口内；symbol/pair 与请求匹配 |

## 9. 批量时间边界 POST 接口

覆盖接口：

| 接口 | Body | Query 必填 |
|---|---|---|
| `/api/binance-full/usdm/meta/kline-time-bounds` | `{"symbols":["BTCUSDT","ETHUSDT"],"interval":"1m"}` | 无 |
| `/api/binance-full/coinm-perp/meta/kline-time-bounds` | `{"symbols":["BTCUSD","ETHUSD"],"interval":"1m"}` | `contract_type=PERPETUAL` |
| `/api/binance-full/coinm-delivery/meta/kline-time-bounds` | `{"symbols":["BTCUSD"],"interval":"1m"}` | `contract_type=CURRENT_QUARTER` |
| `/api/binance-full/usdm-delivery/meta/kline-time-bounds` | `{"symbols":["BTCUSDT"],"interval":"1m"}` | `contract_type=CURRENT_QUARTER` |
| `/api/binance-full/usdm/meta/funding-time-bounds` | `{"symbols":["BTCUSDT","ETHUSDT"]}` | 无 |
| `/api/binance-full/coinm-perp/meta/funding-time-bounds` | `{"symbols":["BTCUSD","ETHUSD"]}` | `contract_type=PERPETUAL` |

| Case ID | 类型 | 适用接口 | 请求/变体 | 预期断言 |
|---|---|---|---|---|
| BF-BATCH-BOUNDS-NORMAL-001 | Normal | 六个接口 | `symbols` 为 JSON 数组 | 成功；`data.items` 为边界行数组 |
| BF-BATCH-BOUNDS-NORMAL-002 | Normal | 六个接口 | `symbols` 为逗号字符串 | 成功；语义等价于数组 |
| BF-BATCH-BOUNDS-BOUNDARY-001 | Boundary | K 线 bounds | `interval=1h` | 成功或无数据提示；不能 500 |
| BF-BATCH-BOUNDS-BOUNDARY-002 | Boundary | 六个接口 | 单个 symbol/pair | 返回 1 个或 0 个 item；结构正确 |
| BF-BATCH-BOUNDS-PARAM-001 | ParamError | 六个接口 | 缺少 body `symbols` | 返回请求体校验错误 |
| BF-BATCH-BOUNDS-PARAM-002 | ParamError | 六个接口 | `symbols=[]` | 返回参数错误或空数组业务提示；不能 500 |
| BF-BATCH-BOUNDS-PARAM-003 | ParamError | 需 contract_type 的接口 | 缺少 query `contract_type` | 返回参数错误 |
| BF-BATCH-BOUNDS-PARAM-004 | ParamError | delivery bounds | `contract_type=PERPETUAL` | 返回参数错误 |
| BF-BATCH-BOUNDS-DQC-001 | DataQuality | 六个接口 | 正常请求 | `min_time_ms/max_time_ms` 非空时为 13 位毫秒；有数据时 `min <= max` |

## 10. 自动化落地优先级

| 优先级 | 范围 | 原因 |
|---|---|---|
| P0 | 元数据、单 symbol/pair Normal、缺必填 ParamError、Response | 先锁住契约和主路径 |
| P1 | K 线/Funding DQC 与 BusinessLogic、多选分桶、批量 POST 两种 body 形态 | 覆盖实际数据质量风险 |
| P2 | `limit_max_detail`、`include_total=true`、Performance、legacy aggregate 开关 | 可能较慢或依赖数据体量，适合独立标记运行 |

