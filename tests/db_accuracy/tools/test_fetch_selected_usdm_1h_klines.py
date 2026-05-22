from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def _workspace_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "tools" / "db_accuracy").is_dir():
            return parent
    raise RuntimeError("Unable to locate QuestTest workspace root")


def _load_script_module():
    script = (
        _workspace_root()
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


def _load_build_module():
    script = (
        _workspace_root()
        / "tools"
        / "db_accuracy"
        / "build_selected_usdm_klines_xlsx.py"
    )
    spec = importlib.util.spec_from_file_location("build_selected_usdm_klines_xlsx", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _install_fake_openpyxl(monkeypatch):
    class FakeCell:
        def __init__(self, value=None):
            self.value = value
            self.data_type = "s"

    class FakeDimension:
        width = None

    class FakeSheet:
        def __init__(self, name):
            self.name = name
            self.column_dimensions = defaultdict(FakeDimension)
            self.rows = []
            self.freeze_panes = None

        def append(self, row):
            self.rows.append(row)

        def __getitem__(self, _key):
            return FakeCell()

        def cell(self, _row, _column):
            return FakeCell()

    class FakeWorkbook:
        def __init__(self, write_only=False):
            self.write_only = write_only
            self.sheets = {}

        def create_sheet(self, name):
            sheet = FakeSheet(name)
            self.sheets[name] = sheet
            return sheet

        def save(self, path):
            Path(path).write_bytes(b"fake xlsx")

    class FakeStyle:
        def __init__(self, *args, **kwargs):
            pass

    openpyxl = types.ModuleType("openpyxl")
    openpyxl.Workbook = FakeWorkbook
    openpyxl.load_workbook = lambda *args, **kwargs: {"对比结果": FakeSheet("对比结果")}

    cell = types.ModuleType("openpyxl.cell")
    cell.WriteOnlyCell = lambda _ws, value=None: FakeCell(value)

    styles = types.ModuleType("openpyxl.styles")
    styles.Alignment = FakeStyle
    styles.Border = FakeStyle
    styles.Font = FakeStyle
    styles.PatternFill = FakeStyle
    styles.Side = FakeStyle

    utils = types.ModuleType("openpyxl.utils")
    utils.get_column_letter = lambda index: chr(64 + index)

    monkeypatch.setitem(sys.modules, "openpyxl", openpyxl)
    monkeypatch.setitem(sys.modules, "openpyxl.cell", cell)
    monkeypatch.setitem(sys.modules, "openpyxl.styles", styles)
    monkeypatch.setitem(sys.modules, "openpyxl.utils", utils)


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


def test_build_xlsx_consumes_fetch_latest_manifest_and_uses_manifest_base(tmp_path, monkeypatch):
    fetch_module = _load_script_module()
    _install_fake_openpyxl(monkeypatch)
    build_module = _load_build_module()
    report_dir = tmp_path / "reports"
    report_dir.mkdir()

    run_base = fetch_module.build_report_base(
        table="binance_kline_all_future_raw",
        interval="1m",
        symbols=["BTCUSDT", "ETHUSDT"],
        all_symbols=False,
        start_ms=1767225600000,
        stamp="20260521_120000",
    )
    manifest_base = "binance_kline_all_future_raw_1m_2symbols"
    diff_csv = report_dir / f"{run_base}_differences.csv"
    db_csv = report_dir / f"{run_base}_db_raw.csv"
    source_csv = report_dir / f"{run_base}_source_raw.csv"
    meta_json = report_dir / f"{run_base}_meta.json"
    manifest = report_dir / f"{manifest_base}_latest_manifest.json"

    diff_csv.write_text(",".join(fetch_module.DIFF_FIELDS) + "\n", encoding="utf-8-sig")
    db_csv.write_text(",".join(fetch_module.DB_RAW_FIELDS) + "\n", encoding="utf-8-sig")
    source_csv.write_text(",".join(fetch_module.SOURCE_RAW_FIELDS) + "\n", encoding="utf-8-sig")
    meta = {
        "generated_at": "2026-05-21 12:00:00",
        "table": "binance_kline_all_future_raw",
        "symbols": ["BTCUSDT", "ETHUSDT"],
        "all_symbols": False,
        "interval": "1m",
        "start_ms": 1767225600000,
        "end_ms": 1767225660000,
        "start_utc": "2026-01-01 00:00:00 UTC",
        "end_utc": "2026-01-01 00:01:00 UTC",
        "range_note": "test",
        "summary_by_symbol": [],
        "db_rows": 0,
        "source_rows": 0,
        "differences": 0,
        "files": {
            "differences_csv": str(diff_csv),
            "db_raw_csv": str(db_csv),
            "source_raw_csv": str(source_csv),
            "meta_json": str(meta_json),
        },
    }
    meta_json.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    old_manifest = report_dir / "binance_usdm_1m_kline_5symbols_latest_manifest.json"
    old_manifest.write_text(json.dumps({**meta, "table": "legacy_name"}, ensure_ascii=False), encoding="utf-8")
    manifest.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    os.utime(old_manifest, (1, 1))

    assert build_module.find_latest_manifest(report_dir) == manifest
    assert build_module.main(["--manifest", str(manifest), "--report-dir", str(report_dir)]) == 0

    assert (report_dir / f"{manifest_base}_latest_zh.xlsx").exists()
    stamped = [
        path
        for path in report_dir.glob(f"{manifest_base}_*_zh.xlsx")
        if not path.name.endswith("_latest_zh.xlsx")
    ]
    assert stamped
    assert not (report_dir / "binance_usdm_1m_kline_5symbols_20240101_to_now_latest_zh.xlsx").exists()
