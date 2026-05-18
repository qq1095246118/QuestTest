# Kline Data 接口测试用例设计

## 1. 测试范围

测试基准地址：`http://54.168.36.173:9020`

本文件覆盖 `docs/x.json` 中 tag 为 `Kline Data` 的 7 个 legacy K 线接口：

| 接口 | 方法 | 说明 |
|---|---|---|
| `/kline/fetch` | GET | 手动触发一次抓取，偏调试入口 |
| `/kline/usdm/meta/time-range` | GET | USDM legacy 单 symbol 时间边界 |
| `/kline/usdm/kline-raw` | GET | USDM legacy raw K 线分页 |
| `/kline/usdm/kline` | GET | USDM legacy curated K 线分页 |
| `/kline/spot/meta/time-range` | GET | Spot legacy 单 symbol 时间边界 |
| `/kline/spot/kline-raw` | GET | Spot legacy raw K 线分页 |
| `/kline/spot/kline` | GET | Spot legacy curated K 线分页 |

不设计鉴权用例；这些接口均按无鉴权访问处理。

## 2. 公共测试数据

| 名称 | 值 | 说明 |
|---|---|---|
| `symbol` | `BTCUSDT` | legacy USDM/Spot 默认测试 symbol |
| `interval` | `1m` | 默认周期 |
| `start_time_ms` | `1704067200000` | 2024-01-01 00:00:00 UTC |
| `end_time_ms` | `1704153600000` | 2024-01-02 00:00:00 UTC |
| `limit_small` | `1` | 分页最小边界 |
| `limit_normal` | `10` | 常规小页，降低性能波动 |
| `limit_max` | `200000` | OpenAPI 标注最大值，仅做边界，不建议纳入常规冒烟 |
| `invalid_symbol` | `NOT_A_SYMBOL` | 非法或无数据 symbol |
| `invalid_interval` | `99m` | 不在常规写入周期内的 interval |

## 3. 公共断言规则

所有成功用例至少校验：

| 断言项 | 预期 |
|---|---|
| HTTP 状态 | 通常为 `200`；业务失败也可能是 HTTP 200 |
| 响应信封 | 包含 `code`、`status`、`message`、`data` |
| 业务成功 | `code == "200"` 且 `status == "success"` |
| 时间戳 | `timestamp`、`close_time`、`min_time_ms`、`max_time_ms` 必须是 13 位毫秒时间戳，允许空值字段除外 |
| 数值字段 | `open/high/low/close/volume/quote_volume` 可转为数字 |
| OHLC | `high >= open/close/low`，`low <= open/close/high` |
| 分页 | `items.length <= limit`，`pagination.limit/offset/include_total` 与请求一致 |
| 时间窗 | 返回行的 `timestamp` 在 `[start_time_ms, end_time_ms)` 内 |

所有异常用例至少校验：

| 断言项 | 预期 |
|---|---|
| 错误识别 | HTTP 为 `400/422`，或响应体 `code` 为 `400/422` |
| 错误信息 | `message` 或 Pydantic `detail` 能定位到错误参数 |
| 不误判成功 | 不允许 `code == "200"` 且 `status == "success"` |

## 4. `/kline/fetch`

| Case ID | 类型 | 请求 | 预期断言 |
|---|---|---|---|
| KD-FETCH-NORMAL-001 | Normal | `GET /kline/fetch?symbol=BTCUSDT&interval=1m&source=binance` | 返回成功信封；`data` 存在；不强制校验 `data` 明细结构，因为该接口是调试抓取入口 |
| KD-FETCH-BOUNDARY-001 | Boundary | `GET /kline/fetch?symbol=BTCUSDT` | 使用默认 `interval=1m`、`source=binance` 语义；返回成功或明确业务错误，但不能 500 |
| KD-FETCH-PARAM-001 | ParamError | 缺少 `symbol` | 返回参数错误；错误信息包含 `symbol` |
| KD-FETCH-PARAM-002 | ParamError | `symbol=` 空字符串 | 返回参数错误或业务错误；不能返回成功数据 |
| KD-FETCH-PARAM-003 | ParamError | `source=unknown` | 返回业务错误或空数据提示；不能 500 |
| KD-FETCH-RESPONSE-001 | Response | 正常请求 | 响应信封字段完整；`message` 非空 |
| KD-FETCH-PERF-001 | Performance | `symbol=BTCUSDT` 最小请求 | 典型环境下响应时间小于 2 秒；若依赖上游实时抓取，可单独标记为非阻塞性能用例 |

## 5. USDM 时间边界

接口：`GET /kline/usdm/meta/time-range`

| Case ID | 类型 | 请求 | 预期断言 |
|---|---|---|---|
| KD-USDM-TIMERANGE-NORMAL-001 | Normal | `symbol=BTCUSDT&interval=1m` | 成功；`data.filters` 存在；`data.raw` 与 `data.curated` 均含 `time_field/min_time_ms/max_time_ms/has_data` |
| KD-USDM-TIMERANGE-BOUNDARY-001 | Boundary | `symbol=BTCUSDT` | 默认 `interval=1m`；返回结构与正常用例一致 |
| KD-USDM-TIMERANGE-BOUNDARY-002 | Boundary | `symbol=BTCUSDT&interval=1h` | 若有数据，`min_time_ms <= max_time_ms`；若无数据，`has_data=false` 且不 500 |
| KD-USDM-TIMERANGE-PARAM-001 | ParamError | 缺少 `symbol` | 返回参数错误，错误信息包含 `symbol` |
| KD-USDM-TIMERANGE-PARAM-002 | ParamError | `symbol=NOT_A_SYMBOL` | 返回业务错误或 `has_data=false`；不能 500 |
| KD-USDM-TIMERANGE-DQC-001 | DataQuality | 正常请求 | `min_time_ms/max_time_ms` 非空时为 13 位毫秒；`has_data=true` 时 `min <= max` |

## 6. USDM raw K 线分页

接口：`GET /kline/usdm/kline-raw`

| Case ID | 类型 | 请求 | 预期断言 |
|---|---|---|---|
| KD-USDM-RAW-NORMAL-001 | Normal | `symbol=BTCUSDT&interval=1m&start_time_ms=1704067200000&end_time_ms=1704153600000&limit=10&offset=0` | 成功；`data.filters/pagination/items` 存在；`items.length <= 10` |
| KD-USDM-RAW-BOUNDARY-001 | Boundary | `limit=1&offset=0` | 返回最多 1 条；分页回显正确 |
| KD-USDM-RAW-BOUNDARY-002 | Boundary | `include_total=true&limit=1` | `pagination.include_total=true`；若返回 `total`，必须为非负整数 |
| KD-USDM-RAW-BOUNDARY-003 | Boundary | 不传时间窗，仅传 `symbol/interval/limit` | 服务按默认时间范围处理；返回成功或明确无数据提示；不能 500 |
| KD-USDM-RAW-PARAM-001 | ParamError | 缺少 `symbol` | 返回参数错误 |
| KD-USDM-RAW-PARAM-002 | ParamError | 只传 `start_time_ms` 不传 `end_time_ms` | 返回参数错误或业务错误，错误信息指向时间窗成对要求 |
| KD-USDM-RAW-PARAM-003 | ParamError | `end_time_ms <= start_time_ms` | 返回参数错误或业务错误 |
| KD-USDM-RAW-PARAM-004 | ParamError | `limit=0` | 返回参数错误 |
| KD-USDM-RAW-PARAM-005 | ParamError | `offset=-1` | 返回参数错误 |
| KD-USDM-RAW-RESPONSE-001 | Response | 正常请求 | 每条 item 至少含 `symbol/timestamp/interval/open/high/low/close/volume` |
| KD-USDM-RAW-DQC-001 | DataQuality | 正常请求 | timestamp 为 13 位毫秒；数值字段可转数字；items 按时间有序 |
| KD-USDM-RAW-LOGIC-001 | BusinessLogic | 正常请求 | 每条 item 满足 OHLC 关系；所有 item 的 symbol 与 interval 与请求一致 |
| KD-USDM-RAW-PERF-001 | Performance | `limit=10` 小窗口请求 | 响应时间小于 2 秒 |

## 7. USDM curated K 线分页

接口：`GET /kline/usdm/kline`

| Case ID | 类型 | 请求 | 预期断言 |
|---|---|---|---|
| KD-USDM-CURATED-NORMAL-001 | Normal | `symbol=BTCUSDT&interval=1m&start_time_ms=1704067200000&end_time_ms=1704153600000&quality_flag=OK&limit=10` | 成功；返回 curated items；若有 item，`quality_flag` 与请求匹配，大小写不敏感 |
| KD-USDM-CURATED-BOUNDARY-001 | Boundary | 不传 `quality_flag` | 不按质量标记过滤；响应结构正确 |
| KD-USDM-CURATED-BOUNDARY-002 | Boundary | `quality_flag=ok` | 与 `OK` 等价或返回明确业务提示；不能 500 |
| KD-USDM-CURATED-PARAM-001 | ParamError | 缺少 `symbol` | 返回参数错误 |
| KD-USDM-CURATED-PARAM-002 | ParamError | 只传 `end_time_ms` 不传 `start_time_ms` | 返回时间窗参数错误 |
| KD-USDM-CURATED-PARAM-003 | ParamError | `limit=200001` | 返回参数错误 |
| KD-USDM-CURATED-RESPONSE-001 | Response | 正常请求 | item 支持 raw K 线字段，并允许含 `quality_flag/repair_tag` |
| KD-USDM-CURATED-DQC-001 | DataQuality | 正常请求 | timestamp 与 close_time 为毫秒；数值字段可转数字 |
| KD-USDM-CURATED-LOGIC-001 | BusinessLogic | 正常请求 | OHLC 合法；时间窗过滤正确 |

## 8. Spot 时间边界

接口：`GET /kline/spot/meta/time-range`

| Case ID | 类型 | 请求 | 预期断言 |
|---|---|---|---|
| KD-SPOT-TIMERANGE-NORMAL-001 | Normal | `symbol=BTCUSDT&interval=1m` | 成功；`raw/curated` 时间边界结构完整 |
| KD-SPOT-TIMERANGE-BOUNDARY-001 | Boundary | 省略 `interval` | 默认 `1m` 语义；不 500 |
| KD-SPOT-TIMERANGE-PARAM-001 | ParamError | 缺少 `symbol` | 返回参数错误 |
| KD-SPOT-TIMERANGE-DQC-001 | DataQuality | 正常请求 | 有数据时 `min_time_ms/max_time_ms` 为 13 位毫秒且 `min <= max` |

## 9. Spot raw K 线分页

接口：`GET /kline/spot/kline-raw`

| Case ID | 类型 | 请求 | 预期断言 |
|---|---|---|---|
| KD-SPOT-RAW-NORMAL-001 | Normal | `symbol=BTCUSDT&interval=1m&start_time_ms=1704067200000&end_time_ms=1704153600000&limit=10` | 成功；items 不超过 limit |
| KD-SPOT-RAW-BOUNDARY-001 | Boundary | `limit=1` | 最多返回 1 条 |
| KD-SPOT-RAW-BOUNDARY-002 | Boundary | `include_total=true` | total 若存在则为非负整数 |
| KD-SPOT-RAW-PARAM-001 | ParamError | 缺少 `symbol` | 返回参数错误 |
| KD-SPOT-RAW-PARAM-002 | ParamError | 时间窗只传一端 | 返回时间窗参数错误 |
| KD-SPOT-RAW-PARAM-003 | ParamError | `limit=0` | 返回参数错误 |
| KD-SPOT-RAW-DQC-001 | DataQuality | 正常请求 | timestamp 毫秒；数值字段可转数字 |
| KD-SPOT-RAW-LOGIC-001 | BusinessLogic | 正常请求 | OHLC 合法；symbol/interval 回显匹配 |

## 10. Spot curated K 线分页

接口：`GET /kline/spot/kline`

| Case ID | 类型 | 请求 | 预期断言 |
|---|---|---|---|
| KD-SPOT-CURATED-NORMAL-001 | Normal | `symbol=BTCUSDT&interval=1m&quality_flag=OK&limit=10` | 成功；curated 数据结构正确 |
| KD-SPOT-CURATED-BOUNDARY-001 | Boundary | 不传 `quality_flag` | 不过滤质量标记；结构正确 |
| KD-SPOT-CURATED-BOUNDARY-002 | Boundary | `quality_flag=ok` | 大小写不敏感或返回明确提示 |
| KD-SPOT-CURATED-PARAM-001 | ParamError | 缺少 `symbol` | 返回参数错误 |
| KD-SPOT-CURATED-PARAM-002 | ParamError | `end_time_ms <= start_time_ms` | 返回参数错误 |
| KD-SPOT-CURATED-RESPONSE-001 | Response | 正常请求 | item 支持 K 线字段和 curated 字段 |
| KD-SPOT-CURATED-DQC-001 | DataQuality | 正常请求 | timestamp 毫秒；数值字段可转数字 |
| KD-SPOT-CURATED-LOGIC-001 | BusinessLogic | 正常请求 | OHLC 合法；时间窗过滤正确 |

## 11. 自动化落地优先级

| 优先级 | 范围 | 原因 |
|---|---|---|
| P0 | 所有接口 Normal、ParamError、Response | 先保证契约可用，避免批量生成后大量误报 |
| P1 | raw/curated K 线 DataQuality、BusinessLogic | 金融数据质量核心风险 |
| P2 | `include_total=true`、`limit_max`、Performance | 大表可能较慢，适合独立标记运行 |

