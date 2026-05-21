"""指定 USDM K 线样本拉取与对比工具。

本工具用于人工生成 DB 与 Binance 源数据的专项 CSV/JSON 报告。
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

WORKSPACE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKSPACE))

from api.external.binance.usdm_market_api import USDMMarketAPI
from infrastructure.database.db_client import DBClient
from services.db_accuracy.compare_service import normalize_value


DEFAULT_TABLE = "binance_kline_all_future_raw"
DEFAULT_INTERVAL = "1m"
DEFAULT_START_MS = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
REPORT_DIR = WORKSPACE / "artifacts" / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

COMPARE_FIELDS = [
    "symbol",
    "interval",
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trades",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
]

DB_RAW_FIELDS = [
    "id",
    "symbol",
    "interval",
    "timestamp",
    "timestamp_utc",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "close_time_utc",
    "quote_volume",
    "trades",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
    "source",
    "created_at",
    "updated_at",
]

DB_SELECT_FIELDS = [field for field in DB_RAW_FIELDS if field not in {"timestamp_utc", "close_time_utc"}]

SOURCE_RAW_FIELDS = [
    "symbol",
    "interval",
    "timestamp",
    "timestamp_utc",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "close_time_utc",
    "quote_volume",
    "trades",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
]

DIFF_FIELDS = ["symbol", "timestamp", "timestamp_utc", "field", "reason", "db_value", "source_value", "note"]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare all-market Binance USD-M DB klines with Binance REST klines and write CSV reports."
    )
    parser.add_argument("--table", default=DEFAULT_TABLE, help="DB table to read, default: binance_kline_all_future_raw")
    parser.add_argument("--interval", default=DEFAULT_INTERVAL, help="Kline interval, default: 1m")
    parser.add_argument(
        "--start-ms",
        type=int,
        default=DEFAULT_START_MS,
        help="Inclusive UTC start timestamp in milliseconds, default: 2026-01-01 00:00:00 UTC",
    )
    parser.add_argument(
        "--end-ms",
        type=int,
        default=None,
        help="Inclusive UTC end timestamp in milliseconds. Defaults to the latest closed kline for --interval.",
    )
    parser.add_argument(
        "--symbol",
        action="append",
        default=[],
        help="Symbol to compare. May be repeated or comma-separated. Defaults to all DB symbols.",
    )
    parser.add_argument(
        "--all-symbols",
        action="store_true",
        help="Discover all distinct symbols in the DB table even if --symbol is provided.",
    )
    parser.add_argument(
        "--report-dir",
        default=str(REPORT_DIR),
        help="Directory for generated CSV/JSON reports, default: ./artifacts/reports",
    )
    return parser.parse_args(argv)


def parse_symbols(values: list[str]) -> list[str]:
    symbols: list[str] = []
    for value in values:
        symbols.extend(part.strip().upper() for part in value.split(",") if part.strip())
    return list(dict.fromkeys(symbols))


def quote_identifier(identifier: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier):
        raise ValueError(f"Unsafe SQL identifier: {identifier}")
    return f"`{identifier}`"


def interval_to_ms(interval: str) -> int:
    match = re.fullmatch(r"(\d+)([mhd])", interval)
    if not match:
        raise ValueError(f"Unsupported kline interval: {interval}")
    amount = int(match.group(1))
    unit = match.group(2)
    multipliers = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}
    return amount * multipliers[unit]


def last_closed_kline_open_ms(interval_ms: int) -> int:
    now_ms = int(time.time() * 1000)
    current_interval_open = now_ms - (now_ms % interval_ms)
    return current_interval_open - interval_ms


def resolve_symbols(
    db: DBClient,
    *,
    explicit_symbols: list[str],
    all_symbols: bool,
    table: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> list[str]:
    if explicit_symbols and not all_symbols:
        return explicit_symbols

    sql = (
        f"SELECT DISTINCT `symbol` FROM {quote_identifier(table)} "
        "WHERE `interval`=%s AND `timestamp` >= %s AND `timestamp` <= %s "
        "ORDER BY `symbol` ASC"
    )
    rows = list(db.query(sql, (interval, start_ms, end_ms)))
    symbols = [str(row["symbol"]).upper() for row in rows if row.get("symbol")]
    if not symbols:
        raise RuntimeError(
            f"No symbols found in {table} for interval={interval}, "
            f"range={ms_to_utc(start_ms)} -> {ms_to_utc(end_ms)}"
        )
    return symbols


def build_report_base(
    *,
    table: str,
    interval: str,
    symbols: list[str],
    all_symbols: bool,
    start_ms: int,
    stamp: str,
) -> str:
    start_day = datetime.fromtimestamp(start_ms / 1000, timezone.utc).strftime("%Y%m%d")
    symbol_scope = "all_symbols" if all_symbols else f"{len(symbols)}symbols"
    return f"{table}_{interval}_{symbol_scope}_{start_day}_to_now_{stamp}"


def ms_to_utc(value: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        return datetime.fromtimestamp(int(value) / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return ""


def text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return str(value)


def write_csv_row(writer: csv.DictWriter, fields: list[str], row: dict[str, Any]) -> None:
    writer.writerow({field: text(row.get(field)) for field in fields})


def db_rows_for_symbol(
    db: DBClient,
    *,
    symbol: str,
    table: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> list[dict[str, Any]]:
    fields = ", ".join(f"`{field}`" for field in DB_SELECT_FIELDS)
    sql = (
        f"SELECT {fields} FROM {quote_identifier(table)} "
        f"WHERE `symbol`=%s AND `interval`=%s AND `timestamp` >= %s AND `timestamp` <= %s "
        f"ORDER BY `timestamp` ASC"
    )
    rows = list(db.query(sql, (symbol, interval, start_ms, end_ms)))
    for row in rows:
        row["timestamp_utc"] = ms_to_utc(row.get("timestamp"))
        row["close_time_utc"] = ms_to_utc(row.get("close_time"))
    return rows


def map_kline(symbol: str, interval: str, raw: list[Any]) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "interval": interval,
        "timestamp": raw[0],
        "timestamp_utc": ms_to_utc(raw[0]),
        "open": raw[1],
        "high": raw[2],
        "low": raw[3],
        "close": raw[4],
        "volume": raw[5],
        "close_time": raw[6],
        "close_time_utc": ms_to_utc(raw[6]),
        "quote_volume": raw[7],
        "trades": raw[8],
        "taker_buy_base_volume": raw[9],
        "taker_buy_quote_volume": raw[10],
    }


def fetch_source_rows(
    api: USDMMarketAPI,
    *,
    symbol: str,
    interval: str,
    interval_ms: int,
    start_ms: int,
    end_ms: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start = start_ms
    while start <= end_ms:
        response = api.get_klines(
            symbol=symbol,
            interval=interval,
            startTime=start,
            endTime=end_ms,
            limit=1000,
        )
        payload = response.json() if hasattr(response, "json") else response
        if not payload:
            break
        mapped = [map_kline(symbol, interval, item) for item in payload]
        rows.extend(mapped)
        last_open = int(mapped[-1]["timestamp"])
        next_start = last_open + interval_ms
        if next_start <= start:
            break
        start = next_start
        time.sleep(0.03)
    return rows


def difference_note(reason: str, field: str) -> str:
    if reason == "missing_db_row":
        return "源接口存在该 K 线，但 DB 中缺少该 open_time"
    if reason == "missing_source_row":
        return "DB 中存在该 K 线，但源接口未返回该 open_time"
    if field in {"open", "high", "low", "close"}:
        return "价格字段不一致"
    if field in {"volume", "quote_volume", "taker_buy_base_volume", "taker_buy_quote_volume"}:
        return "成交量字段不一致"
    if field == "trades":
        return "成交笔数字段不一致"
    if field in {"timestamp", "close_time"}:
        return "K 线时间字段不一致"
    return "字段值不一致"


def compare_symbol(symbol: str, db_rows: list[dict[str, Any]], source_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    differences: list[dict[str, Any]] = []
    db_by_ts = {int(row["timestamp"]): row for row in db_rows}
    source_by_ts = {int(row["timestamp"]): row for row in source_rows}
    for ts in sorted(set(db_by_ts) | set(source_by_ts)):
        db_row = db_by_ts.get(ts)
        source_row = source_by_ts.get(ts)
        if db_row is None:
            differences.append(
                {
                    "symbol": symbol,
                    "timestamp": ts,
                    "timestamp_utc": ms_to_utc(ts),
                    "field": "row",
                    "reason": "missing_db_row",
                    "db_value": "",
                    "source_value": "present",
                    "note": difference_note("missing_db_row", "row"),
                }
            )
            continue
        if source_row is None:
            differences.append(
                {
                    "symbol": symbol,
                    "timestamp": ts,
                    "timestamp_utc": ms_to_utc(ts),
                    "field": "row",
                    "reason": "missing_source_row",
                    "db_value": "present",
                    "source_value": "",
                    "note": difference_note("missing_source_row", "row"),
                }
            )
            continue
        for field in COMPARE_FIELDS:
            db_value = db_row.get(field)
            source_value = source_row.get(field)
            if normalize_value(db_value) != normalize_value(source_value):
                differences.append(
                    {
                        "symbol": symbol,
                        "timestamp": ts,
                        "timestamp_utc": ms_to_utc(ts),
                        "field": field,
                        "reason": "value_mismatch",
                        "db_value": text(db_value),
                        "source_value": text(source_value),
                        "note": difference_note("value_mismatch", field),
                    }
                )
    return differences


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    interval_ms = interval_to_ms(args.interval)
    end_ms = args.end_ms if args.end_ms is not None else last_closed_kline_open_ms(interval_ms)
    if args.start_ms > end_ms:
        raise ValueError(f"--start-ms must be <= end_ms: {args.start_ms} > {end_ms}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    db = DBClient()
    symbols = resolve_symbols(
        db,
        explicit_symbols=parse_symbols(args.symbol),
        all_symbols=args.all_symbols,
        table=args.table,
        interval=args.interval,
        start_ms=args.start_ms,
        end_ms=end_ms,
    )

    base = build_report_base(
        table=args.table,
        interval=args.interval,
        symbols=symbols,
        all_symbols=args.all_symbols,
        start_ms=args.start_ms,
        stamp=stamp,
    )

    diff_csv = report_dir / f"{base}_differences.csv"
    db_csv = report_dir / f"{base}_db_raw.csv"
    source_csv = report_dir / f"{base}_source_raw.csv"
    meta_json = report_dir / f"{base}_meta.json"
    latest_manifest = report_dir / f"{args.table}_{args.interval}_{'all_symbols' if args.all_symbols else str(len(symbols)) + 'symbols'}_latest_manifest.json"

    print("table", args.table, flush=True)
    print("symbols", ",".join(symbols), flush=True)
    print("range", ms_to_utc(args.start_ms), "->", ms_to_utc(end_ms), flush=True)

    api = USDMMarketAPI()
    summary_by_symbol: list[dict[str, Any]] = []
    total_db_rows = 0
    total_source_rows = 0
    total_differences = 0

    with diff_csv.open("w", encoding="utf-8-sig", newline="") as diff_f, db_csv.open("w", encoding="utf-8-sig", newline="") as db_f, source_csv.open("w", encoding="utf-8-sig", newline="") as source_f:
        diff_writer = csv.DictWriter(diff_f, fieldnames=DIFF_FIELDS)
        db_writer = csv.DictWriter(db_f, fieldnames=DB_RAW_FIELDS)
        source_writer = csv.DictWriter(source_f, fieldnames=SOURCE_RAW_FIELDS)
        diff_writer.writeheader()
        db_writer.writeheader()
        source_writer.writeheader()

        try:
            for index, symbol in enumerate(symbols, 1):
                print(f"[{index}/{len(symbols)}] DB {symbol}", flush=True)
                db_rows = db_rows_for_symbol(
                    db,
                    symbol=symbol,
                    table=args.table,
                    interval=args.interval,
                    start_ms=args.start_ms,
                    end_ms=end_ms,
                )
                print(f"[{index}/{len(symbols)}] Binance {symbol}", flush=True)
                source_rows = fetch_source_rows(
                    api,
                    symbol=symbol,
                    interval=args.interval,
                    interval_ms=interval_ms,
                    start_ms=args.start_ms,
                    end_ms=end_ms,
                )
                print(f"[{index}/{len(symbols)}] compare {symbol}: db={len(db_rows)} source={len(source_rows)}", flush=True)
                differences = compare_symbol(symbol, db_rows, source_rows)

                for row in db_rows:
                    write_csv_row(db_writer, DB_RAW_FIELDS, row)
                for row in source_rows:
                    write_csv_row(source_writer, SOURCE_RAW_FIELDS, row)
                for diff in differences:
                    write_csv_row(diff_writer, DIFF_FIELDS, diff)

                reason_counts = Counter(item["reason"] for item in differences)
                field_counts = Counter(item["field"] for item in differences if item["reason"] == "value_mismatch")
                summary_by_symbol.append(
                    {
                        "symbol": symbol,
                        "db_rows": len(db_rows),
                        "source_rows": len(source_rows),
                        "differences": len(differences),
                        "missing_db_rows": reason_counts.get("missing_db_row", 0),
                        "missing_source_rows": reason_counts.get("missing_source_row", 0),
                        "value_mismatches": reason_counts.get("value_mismatch", 0),
                        "top_mismatch_fields": ", ".join(f"{k}:{v}" for k, v in field_counts.most_common(8)),
                        "db_min_time": ms_to_utc(db_rows[0]["timestamp"]) if db_rows else "",
                        "db_max_time": ms_to_utc(db_rows[-1]["timestamp"]) if db_rows else "",
                        "source_min_time": ms_to_utc(source_rows[0]["timestamp"]) if source_rows else "",
                        "source_max_time": ms_to_utc(source_rows[-1]["timestamp"]) if source_rows else "",
                    }
                )
                total_db_rows += len(db_rows)
                total_source_rows += len(source_rows)
                total_differences += len(differences)
                print(f"[{index}/{len(symbols)}] done {symbol}: differences={len(differences)}", flush=True)
        finally:
            db.close()

    meta = {
        "generated_at": generated_at,
        "table": args.table,
        "symbols": symbols,
        "all_symbols": args.all_symbols,
        "interval": args.interval,
        "start_ms": args.start_ms,
        "end_ms": end_ms,
        "start_utc": ms_to_utc(args.start_ms),
        "end_utc": ms_to_utc(end_ms),
        "range_note": f"截至当前已闭合的最后一根 {args.interval} K线，避免未闭合当前周期产生误差",
        "summary_by_symbol": summary_by_symbol,
        "db_rows": total_db_rows,
        "source_rows": total_source_rows,
        "differences": total_differences,
        "files": {
            "differences_csv": str(diff_csv),
            "db_raw_csv": str(db_csv),
            "source_raw_csv": str(source_csv),
            "meta_json": str(meta_json),
        },
    }
    meta_json.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_manifest.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print("meta=", meta_json, flush=True)
    print("latest_manifest=", latest_manifest, flush=True)
    print("diff_csv=", diff_csv, flush=True)
    print("db_csv=", db_csv, flush=True)
    print("source_csv=", source_csv, flush=True)
    print("db_rows=", total_db_rows, "source_rows=", total_source_rows, "differences=", total_differences, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
