# Funding Window and Report Notes Design

## Context

`binance_1h_usdm_funding_rate_raw` direct DB accuracy comparison produced 8,961 `missing_source_row` differences. Spot checks showed the DB rows match Binance `/fapi/v1/fundingRate` when requested with small windows, including `funding_time`, `funding_rate`, `mark_price`, and millisecond offsets.

The false positives happen because direct mode builds funding windows as fixed 90-day spans while the source request uses `limit=1000`. For high-frequency funding rows, a 90-day request can contain more than 1,000 source rows. Binance returns only the first `limit` rows, so rows later in the same window are incorrectly reported as missing from source.

The generated Chinese xlsx report also labels known reasons such as `missing_source_row` as "未归类异常" because the description function defines known notes but does not return them.

## Goals

- Fix direct-mode funding window planning so one source request should not exceed `request_limit`.
- Explicitly support `binance_1h_usdm_funding_rate_raw` as 1-hour funding data.
- Preserve strict `missing_source_row` behavior for real DB-only rows.
- Fix known xlsx reason descriptions so `missing_source_row` and other mapped reasons are not reported as unclassified.

## Non-Goals

- Do not change DB data.
- Do not modify `core/`.
- Do not change cached-mode cache, partition, or DataComPy behavior.
- Do not suppress, downgrade, or ignore `missing_source_row`.
- Do not alter field comparison normalization or equality semantics.

## Design

### Funding Window Planning

Add an explicit funding interval configuration for direct-mode window sizing. For `binance_1h_usdm_funding_rate_raw`, configure the interval as `1h`.

When `DBAccuracyReader.build_windows()` handles `kind: funding`, it should:

- Use the configured funding interval when present.
- Compute the request window span as `interval_ms * request_limit`.
- Keep the existing closed-interval behavior: each window is `[start_ms, end_ms]`, and the next starts at `end_ms + 1`.
- Use a conservative default for funding tables without an explicit interval, preferably `8h`, matching the common standard funding cadence.
- Continue to respect each table's actual DB min/max time range.

This changes the 1h funding direct window from 90 days to about 1,000 hours with the current `request_limit=1000`, preventing source truncation for the observed table.

### Report Note Mapping

In `scripts/build_db_accuracy_allure_xlsx.py`, `_describe_difference()` should return the mapped note when `reason` exists in `notes`.

Known reasons keep their specific Chinese descriptions. Unknown reason strings still fall back to:

```text
未归类异常；字段 <field> 的异常类型为 <reason>。
```

## Testing

Add focused tests for:

- Funding windows with `1h` interval and `request_limit=1000` do not use 90-day spans.
- Funding windows remain continuous without overlap or gaps.
- Funding tables without explicit interval use the conservative default behavior.
- `_describe_difference("missing_source_row", "funding_time")` returns the mapped Chinese explanation, not the unclassified fallback.

Run the relevant unit tests after implementation. Full live DB accuracy runs are optional because they depend on remote MySQL and Binance REST availability.

## Success Criteria

- `binance_1h_usdm_funding_rate_raw` direct mode no longer creates bulk `missing_source_row` false positives caused by `limit=1000` truncation.
- Real DB-only rows still produce `missing_source_row`.
- The xlsx report gives clear known-reason descriptions for `missing_source_row`.
- No changes are made outside the agreed A+C scope.
