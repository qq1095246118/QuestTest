# Binance USDM Funding Rate Raw 结算费率准确性设计

## 背景

`binance_usdm_funding_rate_raw` 已明确为 Binance USDM 真实历史结算费率表，不再承载预计结算费率数据。预计结算费率后续由独立的 `binance_usdm_estimated_funding_rate_raw` 表承载，本次不处理。

`binance_usdm_funding_rate_raw` 的唯一上游来源是 Binance REST：

```text
GET https://fapi.binance.com/fapi/v1/fundingRate
```

该接口返回真实历史结算行，字段包括 `symbol`、`fundingRate`、`fundingTime`、`markPrice`。DB 表中还存在 `funding_rate_interval`，它表达相邻结算时间差；但 Binance `fundingRate` 接口不返回这个字段，所以本次 DB accuracy 不对比它，也不在测试侧重新计算它。

## 目标

- 让 `binance_usdm_funding_rate_raw` 只校验真实结算费率行。
- 继续使用 Binance `/fapi/v1/fundingRate` 作为该表唯一源端。
- DB 与源端按 `symbol + funding_time` 对齐。
- 只对比源端真实返回或可直接映射的字段：`symbol`、`funding_rate`、`funding_time`、`mark_price`。
- funding 请求窗口要同时适配 2h、4h、8h 结算间隔，不能固定假设 8h。
- 预计结算费率表与复算公式留到后续单独设计。

## 非目标

- 不实现 `binance_usdm_estimated_funding_rate_raw`。
- 不为本表调用 `premiumIndexKlines` 或 `markPriceKlines`。
- 不复算预计结算费率公式。
- 不对比或重新计算 `funding_rate_interval`。
- 不修改数据库数据。
- 不修改无关 DB accuracy 行为。

## 设计

### 表语义

`binance_usdm_funding_rate_raw` 的表规格保持为 Binance funding 表：

```yaml
table: binance_usdm_funding_rate_raw
kind: funding
endpoint: usdm_funding
key_fields: [symbol]
time_fields: [funding_time, timestamp]
compare_fields: [symbol, funding_rate, funding_time, mark_price]
request_limit: 1000
```

`funding_rate_interval` 可以存在于 DB 表结构中，但它不是 key field，也不是 DB-to-source accuracy 的 compare field。

### 源端映射

`BinanceSourceService` 继续把 `/fapi/v1/fundingRate` 行映射为：

```text
symbol       <- symbol
funding_rate <- fundingRate
funding_time <- fundingTime
mark_price   <- markPrice
```

源端 row key 仍然是 `funding_time`。direct 和 partitioned 对比时，最终按 `symbol + funding_time` 对齐 DB 与源端行。

### 窗口规划

funding 窗口需要足够保守，保证单次 Binance 请求不会因为超过 `request_limit=1000` 而被截断。

对 `binance_usdm_funding_rate_raw` 来说，当前真实结算间隔中最密的是 2 小时。因此请求窗口跨度按下面规则计算：

```text
2h * request_limit
```

在 `request_limit=1000` 时，一个源端请求覆盖约 2,000 小时。这个跨度对 2h 行是安全的，对 4h 和 8h 行也不会超过 Binance 返回上限。

保留现有闭区间窗口行为：

```text
[start_ms, end_ms]
next_start_ms = end_ms + 1
```

### 对比行为

现有严格对比语义保持不变：

- DB 有、源端没有：`missing_source_row`
- 源端有、DB 没有：`missing_db_row`
- Decimal 归一化后值不同：`value_mismatch`
- 重复 join key 继续走现有重复键失败逻辑

本次改动不能隐藏、降级或忽略真实差异。

## 测试

增加或更新聚焦单测：

- `binance_usdm_funding_rate_raw` 的 funding 窗口按 2h 结算间隔规划。
- funding 窗口连续且不重叠。
- `/fapi/v1/fundingRate` 源端映射仍返回 `symbol`、`funding_rate`、`funding_time`、`mark_price`。
- `funding_rate_interval` 不是源端必需字段，也不在该表 compare fields 中。

完整 live DB accuracy 运行可选，因为它依赖远程 MySQL 与 Binance REST 网络可用性。

## 成功标准

- `binance_usdm_funding_rate_raw` 不再因为 estimated/premium 派生行混入或 funding 窗口过大而产生批量误报的 `missing_source_row`。
- Binance `/fapi/v1/fundingRate` 返回的行会按 `symbol + funding_time` 与 DB 严格对比。
- `funding_rate_interval` 不阻塞、不污染源端对比。
- 本次不引入任何预计结算费率逻辑。
