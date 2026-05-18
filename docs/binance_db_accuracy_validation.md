# Binance DB Accuracy Validation

## Purpose

This manual pytest suite compares Binance raw/metadata rows in MySQL against Binance REST source data. It is designed for full historical validation, not default CI.

## Scope

Included tables are configured in `data/binance_db_accuracy_tables.yaml`.

The first version validates Binance raw/metadata only:

- K line raw tables
- funding raw tables
- COIN-M and USDM delivery raw tables
- `binance_futures_symbols`

The first version does not validate clean/curated tables, CoinGlass tables, DQC issue tables, repair tables, or derived application summaries.

## Run

The selected `--env` must provide working MySQL credentials and Binance base URLs through the normal project settings. A run may fail before comparing data if the database host is not reachable, DNS is unavailable, or Binance rejects/limits requests.

Run all configured Binance DB accuracy checks:

```bash
python3 -m pytest tests/test_binance_db_accuracy.py -v --run-db-accuracy
```

Run one table:

```bash
python3 -m pytest tests/test_binance_db_accuracy.py -v --run-db-accuracy --db-accuracy-table binance_futures_symbols
```

Use a larger safety window when recent rows are still being written or Binance data may still be moving:

```bash
python3 -m pytest tests/test_binance_db_accuracy.py -v --run-db-accuracy --db-accuracy-safety-hours 48
```

## Report

The test attaches two Allure artifacts:

- `db_accuracy_summary`: compact per-table counts
- `db_accuracy_details`: JSON details for every mismatch

The run continues through all configured tables and fails at the end if any mismatch is found.

## Failure Modes

- Unknown `--db-accuracy-table` values fail the run. This prevents a typo from producing a false pass with zero checked tables.
- Time-series tables fail with `no_stable_db_rows` when the safety window leaves no historical rows to validate.
- Per-window DB/API/compare failures are reported as `window_error:*` and the runner continues with the next window/key when possible.
- Per-key window planning failures are reported as `window_planning_error:*` and the runner continues with the next key.
- Table setup failures, such as missing DB columns, are reported as `table_error:*`.
- COIN-M Kline requests are capped to Binance's 200-day request-window limit.
- The suite can make many Binance requests during a full historical run. Use `--db-accuracy-table` for targeted diagnosis when rate limits or network stability are a concern.

## Strictness

Numeric values are compared with Decimal normalization. For example, `1.2300` and `1.23` are equal. No tolerance is applied.

Row keys for time-series comparisons are normalized before matching, so DB timestamps represented as integers, Decimals, or numeric strings match the same upstream timestamp.

## Cached Range Compare Mode

Cached mode is intended for large Binance raw tables where direct full-history comparison would create too many DB queries and Binance REST requests.

The minimum execution unit is a market shard plus time partition:

- USDM/Spot Kline: `symbol + interval + time partition`
- Funding: `symbol + time partition`
- COIN-M perpetual Kline: `symbol + interval + time partition`
- Delivery/continuous Kline: `pair + contract_type + interval + time partition`

Run one explicit USDM Kline market:

```bash
python3 -m pytest tests/test_binance_db_accuracy.py -v \
  --run-db-accuracy \
  --db-accuracy-mode cached \
  --db-accuracy-table binance_kline_all_future_raw \
  --db-accuracy-symbol BTCUSDT \
  --db-accuracy-interval 1m \
  --db-accuracy-start-ms 1704067200000 \
  --db-accuracy-end-ms 1704153599999
```

Run one delivery market:

```bash
python3 -m pytest tests/test_binance_db_accuracy.py -v \
  --run-db-accuracy \
  --db-accuracy-mode cached \
  --db-accuracy-table binance_kline_usdm_delivery_raw \
  --db-accuracy-pair BTCUSDT \
  --db-accuracy-contract-type CURRENT_QUARTER \
  --db-accuracy-interval 1h \
  --db-accuracy-start-ms 1704067200000 \
  --db-accuracy-end-ms 1706745599999
```

Discover market shards from DB for a fixed interval and cap the run:

```bash
python3 -m pytest tests/test_binance_db_accuracy.py -v \
  --run-db-accuracy \
  --db-accuracy-mode cached \
  --db-accuracy-table binance_kline_all_future_raw \
  --db-accuracy-interval 1m \
  --db-accuracy-start-ms 1704067200000 \
  --db-accuracy-end-ms 1704153599999 \
  --db-accuracy-max-shards 20
```

Cache files are written under `.cache/binance_accuracy` by default. Use `--db-accuracy-cache-root` to place cached Binance source data on a larger disk.

Cached mode writes one DataComPy report and one JSON diff summary per compared shard partition under the cache root's `reports/` directory. Binance source partitions with invalid or unavailable markets are recorded in manifest files with `source_market_unavailable`; request and rate-limit failures are recorded as `source_request_failed`.

## Default CI Behavior

The suite is skipped unless `--run-db-accuracy` is passed. Default `pytest` and existing CI flows do not run the full DB accuracy validation.
