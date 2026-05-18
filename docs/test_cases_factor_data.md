# factor-data 接口测试用例设计

## 1. 测试范围

测试基准地址：`http://54.168.36.173:9020`

本文件覆盖 `docs/x.json` 中 tag 为 `factor-data` 的 1 个接口：

| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/v1/factor-data/query` | POST | 清洗表统一查询入口 |

不设计鉴权用例。该接口是 dataset 驱动的统一查询接口，用例重点不是路径数量，而是 dataset、fields、quality_flags、分页 cursor 和排序语义。

## 2. 请求体契约

| 字段 | 类型 | 边界 |
|---|---|---|
| `dataset` | string enum | `kline_data_future`、`kline_data_spot`、`binance_usdm_funding_rate_clean`、`coinglass_open_interest_clean`、`coinglass_global_long_short_account_ratio_clean`、`coinglass_aggregated_taker_buy_sell_volume_clean` |
| `symbols` | string array | 至少 1 个；大写 `BTCUSDT` 形式 |
| `interval` | string or null | K 线和 Coinglass 清洗表常用；`binance_usdm_funding_rate_clean` 不使用 |
| `start_time_ms` | integer | 时间窗起点，Unix 毫秒 |
| `end_time_ms` | integer | 时间窗终点，必须大于 start |
| `fields` | string array | 空数组或省略表示默认字段 |
| `quality_flags` | string array | 常见 `OK`；表须含 `quality_flag` 才有过滤意义 |
| `page_size` | integer | `1..5000` |
| `cursor` | string or null | 翻页游标，首查为空 |
| `sort` | enum | `asc` 或 `desc` |
| `include_symbol_coverage` | boolean | 首查时可统计各 symbol 覆盖 |

## 3. 公共测试数据

| 名称 | 值 |
|---|---|
| `symbols_single` | `["BTCUSDT"]` |
| `symbols_multi` | `["BTCUSDT","ETHUSDT"]` |
| `start_time_ms` | `1704067200000` |
| `end_time_ms` | `1704153600000` |
| `interval` | `1m` |
| `quality_flags` | `["OK"]` |
| `page_size_small` | `1` |
| `page_size_normal` | `100` |
| `page_size_max` | `5000` |

## 4. 公共断言规则

| 断言项 | 预期 |
|---|---|
| 响应信封 | 外层包含 `code/status/message/data` |
| 查询回显 | `data.query` 存在，能反映 dataset、symbols、时间窗等请求条件 |
| 行数据 | `data.rows` 为数组；长度不超过 `page_size` |
| 游标 | `next_cursor` 可为空；`has_more` 为布尔值 |
| 行数 | `row_count_returned == len(rows)` |
| 覆盖率 | `coverage` 可为空；请求 `include_symbol_coverage=true` 且首查时应有稳定结构或明确为空 |
| 最早可用时间 | `earliest_available_time_ms` 存在时为 13 位毫秒 |
| 排序 | `sort=asc` 时间非降序；`sort=desc` 时间非升序 |
| 错误 | 参数错误通过 HTTP `400/422` 或响应体 `code=400/422` 表达；不能 500 |

## 5. dataset 覆盖用例

| Case ID | 类型 | 请求体重点 | 预期断言 |
|---|---|---|---|
| FD-DATASET-KLINE-FUTURE-NORMAL-001 | Normal | `dataset=kline_data_future`，`symbols=["BTCUSDT"]`，`interval=1m`，`quality_flags=["OK"]` | 成功；rows 为期货 K 线清洗数据；时间字段在窗口内 |
| FD-DATASET-KLINE-SPOT-NORMAL-001 | Normal | `dataset=kline_data_spot`，`symbols=["BTCUSDT"]`，`interval=1m`，`quality_flags=["OK"]` | 成功；rows 为 Spot K 线清洗数据 |
| FD-DATASET-BINANCE-FUNDING-NORMAL-001 | Normal | `dataset=binance_usdm_funding_rate_clean`，`interval=null` 或省略 | 成功；rows 为 funding 数据；不依赖 interval |
| FD-DATASET-OI-NORMAL-001 | Normal | `dataset=coinglass_open_interest_clean`，`interval=1h` | 成功；rows 为 OI 清洗数据或空窗提示 |
| FD-DATASET-LS-NORMAL-001 | Normal | `dataset=coinglass_global_long_short_account_ratio_clean`，`interval=1h` | 成功；rows 为多空比清洗数据或空窗提示 |
| FD-DATASET-TAKER-NORMAL-001 | Normal | `dataset=coinglass_aggregated_taker_buy_sell_volume_clean`，`interval=1h` | 成功；rows 为聚合买卖量清洗数据或空窗提示 |
| FD-DATASET-PARAM-001 | ParamError | `dataset=unknown_dataset` | 返回枚举参数错误 |
| FD-DATASET-BOUNDARY-001 | Boundary | 缺少 `dataset` | 按 OpenAPI 默认值使用 `kline_data_future`；响应成功时 `data.query` 应能体现默认 dataset 语义 |

## 6. symbols 用例

| Case ID | 类型 | 请求体重点 | 预期断言 |
|---|---|---|---|
| FD-SYMBOLS-NORMAL-001 | Normal | `symbols=["BTCUSDT"]` | 成功；rows 中 symbol 与请求一致 |
| FD-SYMBOLS-NORMAL-002 | Normal | `symbols=["BTCUSDT","ETHUSDT"]` | 成功；rows symbol 均在请求集合内；coverage 可按 symbol 分组 |
| FD-SYMBOLS-PARAM-001 | ParamError | `symbols=[]` | 返回 `minItems` 校验错误 |
| FD-SYMBOLS-PARAM-002 | ParamError | 缺少 `symbols` | 返回请求体校验错误 |
| FD-SYMBOLS-PARAM-003 | ParamError | `symbols="BTCUSDT"` | 返回类型错误，必须是数组 |
| FD-SYMBOLS-PARAM-004 | ParamError | `symbols=["not_lower_case"]` | 返回空结果、覆盖率无数据或业务提示；不能 500 |

## 7. 时间窗用例

| Case ID | 类型 | 请求体重点 | 预期断言 |
|---|---|---|---|
| FD-TIME-NORMAL-001 | Normal | `start_time_ms=1704067200000`，`end_time_ms=1704153600000` | 成功；rows 时间落在 `[start,end)` |
| FD-TIME-BOUNDARY-001 | Boundary | 很小时间窗，例如 1 分钟 | 成功或空窗提示；不能 500 |
| FD-TIME-PARAM-001 | ParamError | `end_time_ms == start_time_ms` | 返回时间窗错误 |
| FD-TIME-PARAM-002 | ParamError | `end_time_ms < start_time_ms` | 返回时间窗错误 |
| FD-TIME-PARAM-003 | ParamError | 秒级时间戳 `1704067200` | 返回无数据提示或时间粒度错误；若返回 success，必须通过 coverage/earliest_available_time_ms 暴露无数据 |

## 8. fields 与 quality_flags 用例

| Case ID | 类型 | 请求体重点 | 预期断言 |
|---|---|---|---|
| FD-FIELDS-NORMAL-001 | Normal | `fields=["symbol","timestamp","close"]` 用于 K 线 dataset | rows 只返回请求字段和服务保留字段；不返回大量无关列 |
| FD-FIELDS-BOUNDARY-001 | Boundary | `fields=[]` 或省略 | 使用 dataset 默认字段 |
| FD-FIELDS-PARAM-001 | ParamError | `fields=["not_a_column"]` | 返回字段错误或明确业务错误；不能 500 |
| FD-QUALITY-NORMAL-001 | Normal | `quality_flags=["OK"]` | 若 dataset 支持 quality_flag，rows 的质量标记符合请求 |
| FD-QUALITY-BOUNDARY-001 | Boundary | `quality_flags=["ok"]` | 大小写不敏感或返回明确提示 |
| FD-QUALITY-PARAM-001 | ParamError | 对不含 quality_flag 的 dataset 使用 `quality_flags=["OK"]` | 返回明确错误、忽略过滤或空结果；需按实际行为固化，不能 500 |

## 9. 分页和 cursor 用例

| Case ID | 类型 | 请求体重点 | 预期断言 |
|---|---|---|---|
| FD-PAGE-BOUNDARY-001 | Boundary | `page_size=1` | rows 长度不超过 1；`row_count_returned == len(rows)` |
| FD-PAGE-BOUNDARY-002 | Boundary | `page_size=5000` | 不超过 5000；响应不 500 |
| FD-PAGE-PARAM-001 | ParamError | `page_size=0` | 返回参数错误 |
| FD-PAGE-PARAM-002 | ParamError | `page_size=5001` | 返回参数错误 |
| FD-CURSOR-NORMAL-001 | Normal | 首查 `page_size=1`，若 `has_more=true` 用 `next_cursor` 查第二页 | 第二页 rows 与第一页无重复；cursor 请求时 coverage 可为空或不重复计算 |
| FD-CURSOR-PARAM-001 | ParamError | `cursor=invalid_cursor` | 返回游标错误或业务错误；不能 500 |

## 10. 排序用例

| Case ID | 类型 | 请求体重点 | 预期断言 |
|---|---|---|---|
| FD-SORT-NORMAL-001 | Normal | `sort=asc` | rows 按业务时间列与 symbol 联合升序 |
| FD-SORT-NORMAL-002 | Normal | `sort=desc` | rows 按业务时间列与 symbol 联合降序 |
| FD-SORT-PARAM-001 | ParamError | `sort=bad_sort` | 返回枚举参数错误 |

## 11. 自动化落地优先级

| 优先级 | 范围 | 原因 |
|---|---|---|
| P0 | 6 个 dataset Normal、symbols/page_size/sort 参数错误、Response | 先保证统一查询契约 |
| P1 | cursor 翻页、fields、quality_flags、时间窗边界 | 统一查询最容易出现分页和字段裁剪问题 |
| P2 | 秒级时间戳误传、最大 page_size、跨 dataset 深层字段断言 | 依赖真实数据分布，适合稳定后增强 |
