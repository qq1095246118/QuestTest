from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path


def _load_script_module():
    script = (
        Path(__file__).resolve().parents[2]
        / "tools"
        / "db_accuracy"
        / "fetch_selected_usdm_klines.py"
    )
    spec = importlib.util.spec_from_file_location("fetch_selected_usdm_1h_klines", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_default_args_target_all_market_future_raw_from_20260101():
    module = _load_script_module()

    args = module.parse_args([])

    assert args.table == "binance_kline_all_future_raw"
    assert args.interval == "1m"
    assert args.start_ms == int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def test_all_symbols_discovers_distinct_db_symbols_for_window():
    module = _load_script_module()

    class FakeDB:
        def query(self, sql, params):
            assert "SELECT DISTINCT `symbol` FROM `kline_data_future_raw`" in sql
            assert params == ("1m", 1767225600000, 1767311999999)
            return [{"symbol": "BTCUSDT"}, {"symbol": "ETHUSDT"}]

    symbols = module.resolve_symbols(
        FakeDB(),
        explicit_symbols=[],
        all_symbols=True,
        table="kline_data_future_raw",
        interval="1m",
        start_ms=1767225600000,
        end_ms=1767311999999,
    )

    assert symbols == ["BTCUSDT", "ETHUSDT"]


def test_omitting_symbol_defaults_to_all_db_symbols_for_window():
    module = _load_script_module()

    class FakeDB:
        def query(self, sql, params):
            assert "SELECT DISTINCT `symbol` FROM `kline_data_future_raw`" in sql
            assert params == ("1m", 1767225600000, 1767311999999)
            return [{"symbol": "BTCUSDT"}, {"symbol": "ETHUSDT"}, {"symbol": "SOLUSDT"}]

    symbols = module.resolve_symbols(
        FakeDB(),
        explicit_symbols=[],
        all_symbols=False,
        table="kline_data_future_raw",
        interval="1m",
        start_ms=1767225600000,
        end_ms=1767311999999,
    )

    assert symbols == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def test_report_base_names_all_symbols_and_20260101_window():
    module = _load_script_module()

    base = module.build_report_base(
        table="kline_data_future_raw",
        interval="1m",
        symbols=["BTCUSDT", "ETHUSDT"],
        all_symbols=True,
        start_ms=1767225600000,
        stamp="20260521_120000",
    )

    assert base == "kline_data_future_raw_1m_all_symbols_20260101_to_now_20260521_120000"
